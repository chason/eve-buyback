from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Corporation
from app.data.records import CorporationRecord


async def get_by_eve_id(
    session: AsyncSession, eve_corporation_id: int
) -> CorporationRecord | None:
    stmt = select(Corporation).where(Corporation.eve_id == eve_corporation_id)
    corp = (await session.execute(stmt)).scalar_one_or_none()
    return CorporationRecord.model_validate(corp) if corp is not None else None


async def create_corporation(
    session: AsyncSession,
    *,
    eve_corporation_id: int,
    name: str,
    ceo_character_id: int,
    registered_by_character_id: int,
) -> CorporationRecord:
    corp = Corporation(
        eve_id=eve_corporation_id,
        name=name,
        ceo_character_id=ceo_character_id,
        registered_by_character_id=registered_by_character_id,
    )
    session.add(corp)
    await session.flush()  # populate server defaults (registered_at) + the UUID id
    await session.refresh(corp)
    return CorporationRecord.model_validate(corp)
