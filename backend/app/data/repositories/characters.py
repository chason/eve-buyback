from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Character
from app.data.records import CharacterRecord


async def get_by_eve_id(
    session: AsyncSession, eve_character_id: int
) -> CharacterRecord | None:
    char = await _row_by_eve_id(session, eve_character_id)
    return CharacterRecord.model_validate(char) if char is not None else None


async def upsert_character(
    session: AsyncSession, *, eve_character_id: int, name: str
) -> CharacterRecord:
    """Insert the character or refresh its name + last-login timestamp; return the
    record (with its UUID)."""
    char = await _row_by_eve_id(session, eve_character_id)
    if char is None:
        char = Character(eve_id=eve_character_id, name=name)
        session.add(char)
    else:
        char.name = name
        char.last_login_at = datetime.now(UTC)
    await session.flush()
    await session.refresh(char)
    return CharacterRecord.model_validate(char)


async def _row_by_eve_id(
    session: AsyncSession, eve_character_id: int
) -> Character | None:
    stmt = select(Character).where(Character.eve_id == eve_character_id)
    return (await session.execute(stmt)).scalar_one_or_none()
