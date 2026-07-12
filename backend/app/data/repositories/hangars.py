"""Buyback-hangar config persistence (ADR-0044): which corp hangar divisions the
reconciliation counts. Reads return records; the application owns the commit."""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import BuybackHangar
from app.data.records import BuybackHangarRecord


async def list_for_corp(
    session: AsyncSession, corporation_id: uuid.UUID
) -> list[BuybackHangarRecord]:
    rows = (
        (
            await session.execute(
                select(BuybackHangar)
                .where(BuybackHangar.corporation_id == corporation_id)
                .order_by(BuybackHangar.location_name, BuybackHangar.division)
            )
        )
        .scalars()
        .all()
    )
    return [BuybackHangarRecord.model_validate(row) for row in rows]


async def get(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    location_id: str,
    division: int,
) -> BuybackHangarRecord | None:
    row = (
        await session.execute(
            select(BuybackHangar).where(
                BuybackHangar.corporation_id == corporation_id,
                BuybackHangar.location_id == location_id,
                BuybackHangar.division == division,
            )
        )
    ).scalar_one_or_none()
    return BuybackHangarRecord.model_validate(row) if row else None


async def add(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    location_id: str,
    location_name: str,
    division: int,
) -> BuybackHangarRecord:
    hangar = BuybackHangar(
        corporation_id=corporation_id,
        location_id=location_id,
        location_name=location_name,
        division=division,
    )
    session.add(hangar)
    await session.flush()
    await session.refresh(hangar)
    return BuybackHangarRecord.model_validate(hangar)


async def delete_for_corp(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    location_id: str,
    division: int,
) -> bool:
    result = await session.execute(
        delete(BuybackHangar).where(
            BuybackHangar.corporation_id == corporation_id,
            BuybackHangar.location_id == location_id,
            BuybackHangar.division == division,
        )
    )
    return result.rowcount > 0
