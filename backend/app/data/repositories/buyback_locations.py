"""Accepted buyback drop-off locations (ADR-0030). Corp-scoped CRUD; returns records.
The application owns the commit."""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import BuybackLocation
from app.data.records import BuybackLocationRecord
from app.domain.locations import LocationKind


async def list_for_corp(
    session: AsyncSession, corporation_id: uuid.UUID
) -> list[BuybackLocationRecord]:
    stmt = (
        select(BuybackLocation)
        .where(BuybackLocation.corporation_id == corporation_id)
        .order_by(BuybackLocation.name)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [BuybackLocationRecord.model_validate(r) for r in rows]


async def get(
    session: AsyncSession, corporation_id: uuid.UUID, location_id: str
) -> BuybackLocationRecord | None:
    row = await _row(session, corporation_id, location_id)
    return BuybackLocationRecord.model_validate(row) if row is not None else None


async def add(
    session: AsyncSession,
    corporation_id: uuid.UUID,
    *,
    kind: LocationKind,
    location_id: str,
    name: str,
    system_name: str | None,
) -> BuybackLocationRecord:
    """Add the location, or return the existing row if it's already accepted
    (idempotent — `(corporation_id, location_id)` is unique)."""
    existing = await _row(session, corporation_id, location_id)
    if existing is not None:
        return BuybackLocationRecord.model_validate(existing)
    row = BuybackLocation(
        corporation_id=corporation_id,
        kind=kind,
        location_id=location_id,
        name=name,
        system_name=system_name,
    )
    session.add(row)
    await session.flush()
    return BuybackLocationRecord.model_validate(row)


async def delete_for_corp(
    session: AsyncSession, corporation_id: uuid.UUID, location_id: str
) -> bool:
    """Remove a location. Returns whether a row was deleted."""
    result = await session.execute(
        delete(BuybackLocation).where(
            BuybackLocation.corporation_id == corporation_id,
            BuybackLocation.location_id == location_id,
        )
    )
    return result.rowcount > 0


async def _row(
    session: AsyncSession, corporation_id: uuid.UUID, location_id: str
) -> BuybackLocation | None:
    stmt = select(BuybackLocation).where(
        BuybackLocation.corporation_id == corporation_id,
        BuybackLocation.location_id == location_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()
