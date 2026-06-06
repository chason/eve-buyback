"""Corporation + manager use cases. Enforce business rules (who may register,
membership checks, duplicates) and own the unit of work (commit). HTTP status
mapping lives in the interface layer."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.auth import AuthenticatedUser
from app.application.errors import (
    CharacterNotInCorporation,
    CorporationAlreadyRegistered,
    CorporationNotRegistered,
    ManagerAlreadyExists,
    ManagerNotFound,
    NotAuthorized,
)
from app.data.records import CorporationRecord, ManagerRecord
from app.data.repositories import characters as characters_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import managers as managers_repo
from app.plugins.esi import EsiClient


async def register_corporation(
    session: AsyncSession, esi: EsiClient, user: AuthenticatedUser
) -> CorporationRecord:
    """Register the caller's corporation. Allowed for the CEO or a Director (ADR-0015)."""
    if not (user.role == "ceo" or user.is_director):
        raise NotAuthorized(
            "Only the CEO or a Director can register the corporation"
        )
    if await corporations_repo.get_corporation(session, user.corporation_id):
        raise CorporationAlreadyRegistered()

    info = await esi.get_corporation(user.corporation_id)
    corp = await corporations_repo.create_corporation(
        session,
        corporation_id=user.corporation_id,
        name=info.name,
        ceo_character_id=info.ceo_id,
        registered_by_character_id=user.character_id,
    )

    # A non-CEO Director who registers is auto-granted Buyback Manager (ADR-0015).
    if user.role != "ceo":
        await managers_repo.add_manager(
            session,
            corporation_id=user.corporation_id,
            character_id=user.character_id,
            character_name=user.character_name,
            granted_by_character_id=user.character_id,
        )

    await session.commit()
    return corp


async def get_registered_corporation(
    session: AsyncSession, corporation_id: int
) -> CorporationRecord:
    corp = await corporations_repo.get_corporation(session, corporation_id)
    if corp is None:
        raise CorporationNotRegistered()
    return corp


async def list_managers(
    session: AsyncSession, corporation_id: int
) -> list[ManagerRecord]:
    await get_registered_corporation(session, corporation_id)
    return await managers_repo.list_managers(session, corporation_id)


async def add_manager(
    session: AsyncSession,
    esi: EsiClient,
    *,
    corporation_id: int,
    actor_character_id: int,
    target_character_id: int,
) -> ManagerRecord:
    await get_registered_corporation(session, corporation_id)

    target = await esi.get_character(target_character_id)
    if target.corporation_id != corporation_id:
        raise CharacterNotInCorporation()

    await characters_repo.upsert_character(
        session, character_id=target_character_id, name=target.name
    )
    if await managers_repo.manager_exists(
        session, corporation_id=corporation_id, character_id=target_character_id
    ):
        raise ManagerAlreadyExists()

    record = await managers_repo.add_manager(
        session,
        corporation_id=corporation_id,
        character_id=target_character_id,
        character_name=target.name,
        granted_by_character_id=actor_character_id,
    )
    await session.commit()
    return record


async def remove_manager(
    session: AsyncSession, *, corporation_id: int, character_id: int
) -> None:
    await get_registered_corporation(session, corporation_id)
    removed = await managers_repo.remove_manager(
        session, corporation_id=corporation_id, character_id=character_id
    )
    if not removed:
        raise ManagerNotFound()
    await session.commit()
