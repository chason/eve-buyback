"""Lot persistence (ADR-0043): create lots and read them in FIFO order. Consumption
plans are computed by the pure domain (`domain/lots.plan_fifo`) and applied here.
Repositories return records and never commit (data/ conventions)."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Lot
from app.data.records import LotRecord
from app.domain.lots import LotSource


async def create_lot(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    item_type_id: int,
    qty: int,
    unit_purchase_cost: Decimal,
    acquired_at: datetime,
    source: LotSource,
    unit_hauling_cost: Decimal = Decimal(0),
    appraisal_id: uuid.UUID | None = None,
    source_lot_id: uuid.UUID | None = None,
    cost_is_estimated: bool = False,
    location_id: str | None = None,
    notes: str | None = None,
) -> LotRecord:
    lot = Lot(
        corporation_id=corporation_id,
        item_type_id=item_type_id,
        qty_original=qty,
        qty_remaining=qty,
        unit_purchase_cost=unit_purchase_cost,
        unit_hauling_cost=unit_hauling_cost,
        acquired_at=acquired_at,
        source=source,
        appraisal_id=appraisal_id,
        source_lot_id=source_lot_id,
        cost_is_estimated=cost_is_estimated,
        location_id=location_id,
        notes=notes,
    )
    session.add(lot)
    await session.flush()
    await session.refresh(lot)
    return LotRecord.model_validate(lot)


async def open_lots(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    item_type_id: int | None = None,
    location_id: str | None = None,
) -> list[LotRecord]:
    """Open lots (qty_remaining > 0) in FIFO order — oldest acquired first, id as a
    deterministic tiebreak (matching `domain/lots.plan_fifo`'s ordering)."""
    stmt = (
        select(Lot)
        .where(Lot.corporation_id == corporation_id, Lot.qty_remaining > 0)
        .order_by(Lot.acquired_at, Lot.id)
    )
    if item_type_id is not None:
        stmt = stmt.where(Lot.item_type_id == item_type_id)
    if location_id is not None:
        stmt = stmt.where(Lot.location_id == location_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [LotRecord.model_validate(row) for row in rows]


async def consume(
    session: AsyncSession, *, lot_id: uuid.UUID, qty: int
) -> LotRecord:
    """Decrement a lot per one step of a FIFO plan. Never goes below zero — a plan
    that would is a bug upstream, so it raises rather than corrupting the ledger."""
    lot = (
        await session.execute(select(Lot).where(Lot.id == lot_id))
    ).scalar_one()
    if qty > lot.qty_remaining:
        raise ValueError(
            f"cannot consume {qty} from lot {lot_id}: only {lot.qty_remaining} remain"
        )
    lot.qty_remaining -= qty
    await session.flush()
    return LotRecord.model_validate(lot)


async def get_for_corp(
    session: AsyncSession, *, corporation_id: uuid.UUID, lot_id: uuid.UUID
) -> LotRecord | None:
    """One lot, corp-scoped — None for another corp's lot as much as for a missing
    one, so cross-tenant probing is indistinguishable from absence."""
    row = (
        await session.execute(
            select(Lot).where(
                Lot.id == lot_id, Lot.corporation_id == corporation_id
            )
        )
    ).scalar_one_or_none()
    return LotRecord.model_validate(row) if row else None


async def idle_by_location_type(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    location_ids: list[str],
) -> dict[tuple[str, int], int]:
    """The ledger's expected physical stock per `(location_id, type_id)` at the given
    locations — what the hangar reconciliation compares the real count against
    (ADR-0044). Currently `qty_idle == qty_remaining`: nothing tracks market/contract
    escrow or shipments yet (`domain/lots.qty_idle` subtracts them once allocations
    exist, Phases 3–4). Lots with no recorded location can't be attributed to a
    hangar and are excluded."""
    if not location_ids:
        return {}
    rows = (
        await session.execute(
            select(Lot.location_id, Lot.item_type_id, func.sum(Lot.qty_remaining))
            .where(
                Lot.corporation_id == corporation_id,
                Lot.qty_remaining > 0,
                Lot.location_id.in_(location_ids),
            )
            .group_by(Lot.location_id, Lot.item_type_id)
        )
    ).all()
    return {(loc, tid): int(qty) for loc, tid, qty in rows}


async def write_down(
    session: AsyncSession, *, lot_id: uuid.UUID, value: Decimal
) -> LotRecord:
    """Floor a lot's carried value to `value` (ADR-0043 conservatism). Only ever
    downward: raising the carried value back up is a caller bug, so it raises."""
    lot = (
        await session.execute(select(Lot).where(Lot.id == lot_id))
    ).scalar_one()
    if lot.written_down_to is not None and value >= lot.written_down_to:
        raise ValueError(
            f"lot {lot_id} is already written down to {lot.written_down_to}; "
            f"a write-down to {value} would raise it"
        )
    lot.written_down_to = value
    await session.flush()
    return LotRecord.model_validate(lot)


async def exists_for_appraisal(
    session: AsyncSession, appraisal_id: uuid.UUID
) -> bool:
    """Whether lots were already materialized for this appraisal — the idempotency
    check for #151 (the contract watcher fires repeatedly; lots are created once)."""
    row = (
        await session.execute(
            select(Lot.id).where(Lot.appraisal_id == appraisal_id).limit(1)
        )
    ).scalar_one_or_none()
    return row is not None
