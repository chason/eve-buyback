"""Lot-ledger use cases (ADR-0043).

Two halves so far: ingestion (#151 — when the contract watcher, ADR-0037, confirms a
buyback contract completed, the appraisal's accepted lines become inventory lots with a
verified cost basis) and the inventory view (#152 — what the corp owns now, at cost).

Ingestion is deliberately NOT gated by the accounting entitlement (ADR-0042): ESI only
surfaces recent contracts, so skipping unpaid corps would leave permanent holes in a
ledger they later pay to see. The paid gate stays on the read APIs.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.application import entitlements as entitlements_app
from app.application.corporations import get_registered_corporation
from app.data.repositories import appraisals as appraisals_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import sde as sde_repo
from app.domain.contracts import ContractLink
from app.domain.lots import landed_unit_cost


class InventoryLotView(BaseModel):
    """One open lot as the inventory view shows it (#152): what's left, what one
    unit is carried at (landed, write-down floored), and how long it's been sitting."""

    qty: int
    unit_cost: Decimal
    total_cost: Decimal
    acquired_at: datetime
    days_held: int
    stale: bool
    cost_is_estimated: bool


class InventoryItemView(BaseModel):
    """One item type's holdings: its open lots plus the rollup the table row shows.
    `type_name` is None when the type is missing from the seeded SDE."""

    type_id: int
    type_name: str | None
    qty: int
    total_cost: Decimal
    oldest_days: int
    stale: bool
    any_estimated: bool
    lots: list[InventoryLotView]


class InventoryView(BaseModel):
    """The whole "What we've got" view (ADR-0043, #152): inventory carried at cost,
    with verified and estimated cost kept apart so they never silently blend."""

    total_cost: Decimal
    verified_cost: Decimal
    estimated_cost: Decimal
    stale_days: int
    items: list[InventoryItemView]


async def get_inventory(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    stale_days: int,
    now: datetime | None = None,
) -> InventoryView:
    """What the corp's buyback owns right now, at cost (#152): every open lot rolled
    up per item type, oldest-first within a type (FIFO order), items sorted by what
    was paid (biggest holdings first). A lot sitting `stale_days` or longer is
    flagged. Gated: the accounting entitlement is required (ADR-0042)."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting", now=now
    )
    now = now or datetime.now(UTC)

    lots = await lots_repo.open_lots(session, corporation_id=corp.id)
    names = await sde_repo.get_types(session, sorted({lot.item_type_id for lot in lots}))

    by_type: dict[int, list[InventoryLotView]] = {}
    estimated_flags: dict[int, bool] = {}
    for lot in lots:  # already FIFO-ordered: oldest acquired first
        unit_cost = landed_unit_cost(
            lot.unit_purchase_cost, lot.unit_hauling_cost, lot.written_down_to
        )
        days_held = max(0, (now - lot.acquired_at).days)
        by_type.setdefault(lot.item_type_id, []).append(
            InventoryLotView(
                qty=lot.qty_remaining,
                unit_cost=unit_cost,
                total_cost=lot.qty_remaining * unit_cost,
                acquired_at=lot.acquired_at,
                days_held=days_held,
                stale=days_held >= stale_days,
                cost_is_estimated=lot.cost_is_estimated,
            )
        )
        estimated_flags[lot.item_type_id] = (
            estimated_flags.get(lot.item_type_id, False) or lot.cost_is_estimated
        )

    items = [
        InventoryItemView(
            type_id=type_id,
            type_name=names[type_id].name if type_id in names else None,
            qty=sum(v.qty for v in views),
            total_cost=sum((v.total_cost for v in views), Decimal(0)),
            oldest_days=max(v.days_held for v in views),
            stale=any(v.stale for v in views),
            any_estimated=estimated_flags[type_id],
            lots=views,
        )
        for type_id, views in by_type.items()
    ]
    items.sort(key=lambda item: item.total_cost, reverse=True)

    estimated_cost = sum(
        (v.total_cost for item in items for v in item.lots if v.cost_is_estimated),
        Decimal(0),
    )
    total_cost = sum((item.total_cost for item in items), Decimal(0))
    return InventoryView(
        total_cost=total_cost,
        verified_cost=total_cost - estimated_cost,
        estimated_cost=estimated_cost,
        stale_days=stale_days,
        items=items,
    )


async def materialize_buyback_lots(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    links: Sequence[ContractLink],
    now: datetime,
) -> int:
    """Create the lots for appraisals whose contract just completed (#151): one lot per
    accepted line at the exact price paid (`unit_price` — so `cost_is_estimated` stays
    False), sitting at the appraisal's delivery location, acquired when the contract
    completed. Idempotent per appraisal: `completed` is terminal for lot creation, so
    an appraisal that already has lots is never touched again, whatever the watcher
    later observes. Returns the number of lots created.

    Runs inside the watcher's unit of work — the caller owns the commit, so the link
    update and the lots it implies land atomically."""
    pending = [
        link
        for link in links
        if link.status == "completed"
        and not await lots_repo.exists_for_appraisal(session, link.appraisal_id)
    ]
    if not pending:
        return 0

    ids = [link.appraisal_id for link in pending]
    lines_by_appraisal = await appraisals_repo.accepted_lines_for_lots(session, ids)
    facts = await appraisals_repo.match_facts(session, ids)

    created = 0
    for link in pending:
        _, location_id = facts.get(link.appraisal_id, (None, None))
        for line in lines_by_appraisal.get(link.appraisal_id, []):
            await lots_repo.create_lot(
                session,
                corporation_id=corporation_id,
                item_type_id=line.type_id,
                qty=line.quantity,
                unit_purchase_cost=line.unit_price,
                acquired_at=link.completed_at or now,
                source="buyback",
                appraisal_id=link.appraisal_id,
                location_id=location_id,
            )
            created += 1
    return created
