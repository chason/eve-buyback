"""Reprocess-transformation use cases (ADR-0047, #177): a manager records that a
lot's units were reprocessed, and the cost the corp actually paid flows into child
material lots — instead of the materials reappearing later as re-estimated hangar
discoveries with severed lineage (exactly what ADR-0044 must not do).

Source-agnostic by design: ore, modules, ships, salvage — anything with yield data
pre-fills, and anything at all can be recorded with hand-entered outputs.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.application import entitlements as entitlements_app
from app.application.corporations import get_registered_corporation
from app.application.errors import LotNotFound, ReprocessQtyUnavailable
from app.data.records import LotRecord
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import prices as prices_repo
from app.data.repositories import sde as sde_repo
from app.data.repositories import transformations as transformations_repo
from app.domain.lots import landed_unit_cost
from app.domain.transformations import (
    OutputLine,
    allocate_source_cost,
    base_yield_outputs,
)


@dataclass(frozen=True)
class ReprocessPreview:
    """What the record dialog opens with: the source lot and the pre-filled outputs
    (base yields where known — editable, because real yields vary)."""

    lot: LotRecord
    source_type_name: str | None
    outputs: list[tuple[int, str | None, int]]  # (type_id, name, prefill qty)


async def preview_reprocess(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    lot_id: uuid.UUID,
    qty: int | None = None,
) -> ReprocessPreview:
    """The pre-filled reprocess form for a lot (ADR-0047): output quantities from
    the type's base yields at the app's assumed rate (ADR-0026), empty when the
    type has no yield data (the manager enters outputs by hand). Gated."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    lot = await lots_repo.get_for_corp(session, corporation_id=corp.id, lot_id=lot_id)
    if lot is None:
        raise LotNotFound()
    qty = qty or lot.qty_remaining

    types = await sde_repo.get_types(session, [lot.item_type_id])
    source_type = types.get(lot.item_type_id)
    materials = (
        await sde_repo.get_type_materials(session, [lot.item_type_id])
    ).get(lot.item_type_id, [])
    prefill = base_yield_outputs(
        qty, source_type.portion_size if source_type else 1, materials
    )
    names = await sde_repo.get_types(session, sorted(prefill))
    return ReprocessPreview(
        lot=lot,
        source_type_name=source_type.name if source_type else None,
        outputs=[
            (tid, names[tid].name if tid in names else None, out_qty)
            for tid, out_qty in sorted(prefill.items())
        ],
    )


async def record_reprocess(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    lot_id: uuid.UUID,
    qty: int,
    outputs: dict[int, int],
    recorded_by_character_id: int | None = None,
    now: datetime | None = None,
) -> list[LotRecord]:
    """Record that `qty` units of the lot were reprocessed into `outputs`
    (ADR-0047): consume the source, write the transformation audit row, and create
    one child lot per output whose combined basis equals exactly the source cost
    consumed — allocated pro-rata by market value at split-off (cached prices at
    the corp's default hub). `cost_is_estimated` and `acquired_at` inherit: the
    children are the same capital, aged from when it was bought, exactly as certain
    as its cost was. Owns the commit; returns the children."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    lot = await lots_repo.get_for_corp(session, corporation_id=corp.id, lot_id=lot_id)
    if lot is None:
        raise LotNotFound()
    if qty <= 0 or qty > lot.qty_remaining:
        raise ReprocessQtyUnavailable()
    now = now or datetime.now(UTC)

    consumed_cost = qty * landed_unit_cost(
        lot.unit_purchase_cost, lot.unit_hauling_cost, lot.written_down_to
    )
    values = await _split_off_values(session, corp.id, sorted(outputs))
    allocated = allocate_source_cost(
        consumed_cost,
        [
            OutputLine(
                type_id=tid, quantity=out_qty, unit_value=values.get(tid)
            )
            for tid, out_qty in sorted(outputs.items())
        ],
    )

    await lots_repo.consume(session, lot_id=lot.id, qty=qty)
    await transformations_repo.create_transformation(
        session,
        corporation_id=corp.id,
        source_lot_id=lot.id,
        qty_consumed=qty,
        occurred_at=now,
        recorded_by_character_id=recorded_by_character_id,
    )
    children = [
        await lots_repo.create_lot(
            session,
            corporation_id=corp.id,
            item_type_id=out.type_id,
            qty=out.quantity,
            unit_purchase_cost=out.unit_cost,
            acquired_at=lot.acquired_at,  # inherited: same capital, same age
            source="reprocess",
            source_lot_id=lot.id,
            cost_is_estimated=lot.cost_is_estimated,  # inherited, never blended
            location_id=lot.location_id,
        )
        for out in allocated
    ]
    await session.commit()
    return children


async def _split_off_values(
    session: AsyncSession, corporation_id: uuid.UUID, type_ids: list[int]
) -> dict[int, Decimal]:
    """Market unit value per output type at split-off: the buy-side aggregate at
    the corp's default hub from the cache (the same convention as the ADR-0026
    reprocess pricing and the #153 valuation). Absent types simply carry no
    weight in the allocation."""
    config = await config_repo.get_config(session, corporation_id)
    if config is None or not type_ids:
        return {}
    out: dict[int, Decimal] = {}
    for price in await prices_repo.get_prices(
        session, hub_id=config.market_hub_id, type_ids=type_ids
    ):
        if price.buy_order_count > 0:
            out[price.type_id] = getattr(
                price, f"buy_{config.aggregate_field}"
            )
    return out
