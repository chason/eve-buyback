from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Corporation
from app.data.records import CorporationRecord


async def get_corporation(
    session: AsyncSession, corporation_id: int
) -> CorporationRecord | None:
    corp = await session.get(Corporation, corporation_id)
    return CorporationRecord.model_validate(corp) if corp is not None else None


async def create_corporation(
    session: AsyncSession,
    *,
    corporation_id: int,
    name: str,
    ceo_character_id: int,
    registered_by_character_id: int,
) -> CorporationRecord:
    corp = Corporation(
        corporation_id=corporation_id,
        name=name,
        ceo_character_id=ceo_character_id,
        registered_by_character_id=registered_by_character_id,
    )
    session.add(corp)
    await session.flush()  # populate server defaults (registered_at)
    await session.refresh(corp)
    return CorporationRecord.model_validate(corp)
