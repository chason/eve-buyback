"""Hangar-snapshot persistence (ADR-0050): the `(location, type) → qty` photograph
a hangar sync took of the marked buyback hangars, plus the one-row `hangar_sync`
marker saying when. Replaced wholesale per sync; the application owns the commit."""

import uuid
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import HangarStock, HangarSync
from app.data.records import HangarStockRecord


async def replace_for_corp(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    counts: dict[tuple[str, int], int],
    synced_at: datetime,
) -> None:
    """Overwrite the corp's snapshot with what this sync counted and stamp
    `synced_at`. An empty `counts` is a valid snapshot (the hangar is empty) —
    the stamp is what distinguishes it from "never looked"."""
    await session.execute(
        delete(HangarStock).where(HangarStock.corporation_id == corporation_id)
    )
    for (location_id, type_id), qty in sorted(counts.items()):
        session.add(
            HangarStock(
                corporation_id=corporation_id,
                location_id=location_id,
                type_id=type_id,
                qty=qty,
            )
        )
    marker = (
        await session.execute(
            select(HangarSync).where(HangarSync.corporation_id == corporation_id)
        )
    ).scalar_one_or_none()
    if marker is None:
        session.add(HangarSync(corporation_id=corporation_id, synced_at=synced_at))
    else:
        marker.synced_at = synced_at
    await session.flush()


async def list_for_corp(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> list[HangarStockRecord]:
    rows = (
        (
            await session.execute(
                select(HangarStock)
                .where(HangarStock.corporation_id == corporation_id)
                .order_by(HangarStock.location_id, HangarStock.type_id)
            )
        )
        .scalars()
        .all()
    )
    return [HangarStockRecord.model_validate(row) for row in rows]


async def synced_at(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> datetime | None:
    """When the corp's snapshot was last taken — None means never (the Stock page
    falls back to the ledger view)."""
    return (
        await session.execute(
            select(HangarSync.synced_at).where(
                HangarSync.corporation_id == corporation_id
            )
        )
    ).scalar_one_or_none()


async def clear_for_corp(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> None:
    """Drop the snapshot and its marker — the privacy cleanup when a corp unmarks
    its last buyback hangar (the stored contents are no longer wanted)."""
    await session.execute(
        delete(HangarStock).where(HangarStock.corporation_id == corporation_id)
    )
    await session.execute(
        delete(HangarSync).where(HangarSync.corporation_id == corporation_id)
    )
