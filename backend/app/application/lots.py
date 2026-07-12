"""Lot-ledger use cases (ADR-0043).

Three pieces so far: ingestion (#151 — when the contract watcher, ADR-0037, confirms a
buyback contract completed, the appraisal's accepted lines become inventory lots with a
verified cost basis), the inventory view (#152 — what the corp owns now, at cost, and
what it would fetch today, #153), and the automatic write-down sweep (#153).

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
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import expenses as expenses_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import prices as prices_repo
from app.data.repositories import sde as sde_repo
from app.domain.contracts import ContractLink
from app.domain.lots import landed_unit_cost, nrv_per_unit, write_down_target


async def _nrv_by_type(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    type_ids: list[int],
    sales_tax_rate: Decimal,
) -> dict[int, Decimal]:
    """Per type, what one unit would net if sold today (#153): the buy-side aggregate
    at the corp's configured default hub — the same field family appraisals price
    with — net of sales tax. Best-available *cached* value (ADR-0043): reads the
    `MarketPrice` cache as-is, never fetches live; unpriced/unwarmed types are simply
    absent and the callers surface that rather than invent a value."""
    if not type_ids:
        return {}
    config = await config_repo.get_config(session, corporation_id)
    if config is None:
        return {}
    prices = await prices_repo.get_prices(
        session, hub_id=config.market_hub_id, type_ids=type_ids
    )
    out: dict[int, Decimal] = {}
    for price in prices:
        buy = (
            getattr(price, f"buy_{config.aggregate_field}")
            if price.buy_order_count > 0
            else None
        )
        if buy is not None:
            out[price.type_id] = nrv_per_unit(buy, sales_tax_rate=sales_tax_rate)
    return out


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
    `type_name` is None when the type is missing from the seeded SDE. `worth` /
    `unrealized` (#153) are None when the type has no cached market price — surfaced,
    never invented."""

    type_id: int
    type_name: str | None
    qty: int
    total_cost: Decimal
    oldest_days: int
    stale: bool
    any_estimated: bool
    worth: Decimal | None = None
    unrealized: Decimal | None = None
    lots: list[InventoryLotView]


class InventoryView(BaseModel):
    """The whole "What we've got" view (ADR-0043, #152/#153): inventory carried at
    cost, with verified and estimated cost kept apart so they never silently blend.
    `worth_total` is "if we sold it all today" (net of sales tax) and
    `unrealized_total` the paper gain/loss — its OWN line, never folded into assets;
    both cover only the types the market cache can price (`unpriced_types` counts
    the rest)."""

    total_cost: Decimal
    verified_cost: Decimal
    estimated_cost: Decimal
    stale_days: int
    worth_total: Decimal
    unrealized_total: Decimal
    unpriced_types: int
    items: list[InventoryItemView]


async def get_inventory(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    stale_days: int,
    sales_tax_rate: Decimal,
    now: datetime | None = None,
) -> InventoryView:
    """What the corp's buyback owns right now, at cost (#152), and what it would
    fetch today (#153): every open lot rolled up per item type, oldest-first within
    a type (FIFO order), items sorted by what was paid (biggest holdings first). A
    lot sitting `stale_days` or longer is flagged. Gated: the accounting entitlement
    is required (ADR-0042)."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting", now=now
    )
    now = now or datetime.now(UTC)

    lots = await lots_repo.open_lots(session, corporation_id=corp.id)
    type_ids = sorted({lot.item_type_id for lot in lots})
    names = await sde_repo.get_types(session, type_ids)
    nrv = await _nrv_by_type(
        session,
        corporation_id=corp.id,
        type_ids=type_ids,
        sales_tax_rate=sales_tax_rate,
    )

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

    items = []
    for type_id, views in by_type.items():
        qty = sum(v.qty for v in views)
        total_cost = sum((v.total_cost for v in views), Decimal(0))
        worth = qty * nrv[type_id] if type_id in nrv else None
        items.append(
            InventoryItemView(
                type_id=type_id,
                type_name=names[type_id].name if type_id in names else None,
                qty=qty,
                total_cost=total_cost,
                oldest_days=max(v.days_held for v in views),
                stale=any(v.stale for v in views),
                any_estimated=estimated_flags[type_id],
                worth=worth,
                unrealized=worth - total_cost if worth is not None else None,
                lots=views,
            )
        )
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
        worth_total=sum(
            (item.worth for item in items if item.worth is not None), Decimal(0)
        ),
        unrealized_total=sum(
            (item.unrealized for item in items if item.unrealized is not None),
            Decimal(0),
        ),
        unpriced_types=sum(1 for item in items if item.worth is None),
        items=items,
    )


async def apply_write_downs(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    sales_tax_rate: Decimal,
    now: datetime | None = None,
) -> int:
    """The conservatism sweep (ADR-0043, #153): for every open lot whose current
    market value (NRV) fell below its carried cost, floor the carried value
    (`written_down_to = NRV`) and book the loss as a `write_down` expense in this
    period. Never reverses upward — a later price rise shows as unrealized gain from
    the floored base, not as a restored cost. Idempotent at stable prices: once
    floored, landed cost == NRV, so no further target exists until prices drop
    again (which books only the *incremental* loss). Returns lots written down.

    Run by the background job for entitled corps; the job owns the corp filter, so
    this stays callable per corp. Owns its unit of work."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    now = now or datetime.now(UTC)
    lots = await lots_repo.open_lots(session, corporation_id=corp.id)
    if not lots:
        return 0
    nrv = await _nrv_by_type(
        session,
        corporation_id=corp.id,
        type_ids=sorted({lot.item_type_id for lot in lots}),
        sales_tax_rate=sales_tax_rate,
    )

    written_down = 0
    for lot in lots:
        value = nrv.get(lot.item_type_id)
        if value is None:
            continue  # no cached price → no evidence to book a loss on
        landed = landed_unit_cost(
            lot.unit_purchase_cost, lot.unit_hauling_cost, lot.written_down_to
        )
        target = write_down_target(landed, value)
        if target is None:
            continue
        await lots_repo.write_down(session, lot_id=lot.id, value=target)
        await expenses_repo.create_expense(
            session,
            corporation_id=corp.id,
            kind="write_down",
            amount=(landed - target) * lot.qty_remaining,
            source="system",
            incurred_at=now,
            lot_id=lot.id,
            note="Stock is worth less than we paid; its value was lowered to match "
            "the market.",
        )
        written_down += 1
    if written_down:
        await session.commit()
    return written_down


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
