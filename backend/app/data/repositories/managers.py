from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Character, ManagerAssignment
from app.data.records import ManagerRecord


async def manager_exists(
    session: AsyncSession, *, corporation_id: int, character_id: int
) -> bool:
    result = await session.execute(
        select(ManagerAssignment).where(
            ManagerAssignment.corporation_id == corporation_id,
            ManagerAssignment.character_id == character_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def list_managers(
    session: AsyncSession, corporation_id: int
) -> list[ManagerRecord]:
    stmt = (
        select(ManagerAssignment, Character.name)
        .join(Character, Character.character_id == ManagerAssignment.character_id)
        .where(ManagerAssignment.corporation_id == corporation_id)
    )
    rows = (await session.execute(stmt)).all()
    return [
        ManagerRecord(
            character_id=assignment.character_id,
            character_name=name,
            granted_by_character_id=assignment.granted_by_character_id,
            granted_at=assignment.granted_at,
        )
        for assignment, name in rows
    ]


async def add_manager(
    session: AsyncSession,
    *,
    corporation_id: int,
    character_id: int,
    character_name: str,
    granted_by_character_id: int,
) -> ManagerRecord:
    assignment = ManagerAssignment(
        corporation_id=corporation_id,
        character_id=character_id,
        granted_by_character_id=granted_by_character_id,
    )
    session.add(assignment)
    await session.flush()  # populate server defaults (granted_at)
    await session.refresh(assignment)
    return ManagerRecord(
        character_id=character_id,
        character_name=character_name,
        granted_by_character_id=granted_by_character_id,
        granted_at=assignment.granted_at,
    )


async def remove_manager(
    session: AsyncSession, *, corporation_id: int, character_id: int
) -> bool:
    """Delete the assignment; return False if it didn't exist."""
    result = await session.execute(
        select(ManagerAssignment).where(
            ManagerAssignment.corporation_id == corporation_id,
            ManagerAssignment.character_id == character_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if assignment is None:
        return False
    await session.delete(assignment)
    return True
