from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Character
from app.data.records import CharacterRecord


async def get_character(
    session: AsyncSession, character_id: int
) -> CharacterRecord | None:
    char = await session.get(Character, character_id)
    return CharacterRecord.model_validate(char) if char is not None else None


async def upsert_character(
    session: AsyncSession, *, character_id: int, name: str
) -> None:
    """Insert the character or refresh its name + last-login timestamp."""
    char = await session.get(Character, character_id)
    if char is None:
        session.add(Character(character_id=character_id, name=name))
    else:
        char.name = name
        char.last_login_at = datetime.now(UTC)
