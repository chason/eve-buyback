"""Declared-shipment persistence (ADR-0049, #208): create hauls, read the open
list, mark arrivals, and the per-slot open sums the reconciliation excludes from
idle at both ends. The application owns the commit."""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Shipment
from app.data.records import ShipmentRecord


async def add(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    type_id: int,
    origin_location_id: str,
    destination_location_id: str,
    qty: int,
) -> ShipmentRecord:
    shipment = Shipment(
        corporation_id=corporation_id,
        type_id=type_id,
        origin_location_id=origin_location_id,
        destination_location_id=destination_location_id,
        qty=qty,
        status="open",
    )
    session.add(shipment)
    await session.flush()
    await session.refresh(shipment)
    return ShipmentRecord.model_validate(shipment)


async def get_for_corp(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    shipment_id: uuid.UUID,
) -> ShipmentRecord | None:
    """One shipment, corp-scoped — None for another corp's shipment as much as
    for a missing one, so cross-tenant probing is indistinguishable from absence
    (the `lots.get_for_corp` posture)."""
    row = (
        await session.execute(
            select(Shipment).where(
                Shipment.id == shipment_id,
                Shipment.corporation_id == corporation_id,
            )
        )
    ).scalar_one_or_none()
    return ShipmentRecord.model_validate(row) if row else None


async def mark_arrived(
    session: AsyncSession, *, shipment_id: uuid.UUID, arrived_at: datetime
) -> ShipmentRecord:
    """Close a haul (open → arrived). The application decides the transition
    rules; this only writes it."""
    row = (
        await session.execute(select(Shipment).where(Shipment.id == shipment_id))
    ).scalar_one()
    row.status = "arrived"
    row.arrived_at = arrived_at
    await session.flush()
    return ShipmentRecord.model_validate(row)


async def list_open(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> list[ShipmentRecord]:
    rows = (
        (
            await session.execute(
                select(Shipment)
                .where(
                    Shipment.corporation_id == corporation_id,
                    Shipment.status == "open",
                )
                .order_by(Shipment.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [ShipmentRecord.model_validate(row) for row in rows]


async def open_by_origin_type(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> dict[tuple[str, int], int]:
    """Units on the road per `(origin_location_id, type_id)` — physically out of
    the origin hangar, so the reconciliation subtracts them from expected stock
    there, and a new haul can't claim them again (`qty_idle`, ADR-0043)."""
    return await _open_sums(session, corporation_id, Shipment.origin_location_id)


async def open_by_destination_type(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> dict[tuple[str, int], int]:
    """Units on the road per `(destination_location_id, type_id)` — goods that
    may already sit at the destination unmarked, so the reconciliation absorbs
    that much excess there instead of booking it as off-app stock."""
    return await _open_sums(
        session, corporation_id, Shipment.destination_location_id
    )


async def _open_sums(
    session: AsyncSession, corporation_id: uuid.UUID, location_column
) -> dict[tuple[str, int], int]:
    rows = (
        await session.execute(
            select(location_column, Shipment.type_id, func.sum(Shipment.qty))
            .where(
                Shipment.corporation_id == corporation_id,
                Shipment.status == "open",
            )
            .group_by(location_column, Shipment.type_id)
        )
    ).all()
    return {(loc, tid): int(qty) for loc, tid, qty in rows}
