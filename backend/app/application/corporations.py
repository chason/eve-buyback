"""Corporation + manager use cases. Enforce business rules (who may register,
membership checks, duplicates) and own the unit of work (commit). HTTP status
mapping lives in the interface layer.

These use cases speak EVE ids at their boundary (from the session) and resolve them
to the internal UUIDs (ADR-0025) before touching child tables."""

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
from app.config import get_settings
from app.data.records import CorporationRecord, ManagerRecord
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import characters as characters_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import managers as managers_repo
from app.domain.pricing import (
    DEFAULT_AGGREGATE_FIELD,
    DEFAULT_BASIS,
    DEFAULT_PERCENTAGE,
)
from app.plugins.esi import EsiClient


async def register_corporation(
    session: AsyncSession, esi: EsiClient, user: AuthenticatedUser
) -> CorporationRecord:
    """Register the caller's corporation. Allowed for the CEO or a Director (ADR-0015)."""
    if not (user.role == "ceo" or user.is_director):
        raise NotAuthorized(
            "Only the CEO or a Director can register the corporation"
        )
    if await corporations_repo.get_by_eve_id(session, user.corporation_id):
        raise CorporationAlreadyRegistered()

    info = await esi.get_corporation(user.corporation_id)
    corp = await corporations_repo.create_corporation(
        session,
        eve_corporation_id=user.corporation_id,
        name=info.name,
        ceo_character_id=info.ceo_id,
        registered_by_character_id=user.character_id,
    )

    # A non-CEO Director who registers is auto-granted Buyback Manager (ADR-0015).
    if user.role != "ceo":
        char = await characters_repo.upsert_character(
            session, eve_character_id=user.character_id, name=user.character_name
        )
        await managers_repo.add_manager(
            session,
            corporation_id=corp.id,
            character_id=char.id,
            character_eve_id=user.character_id,
            character_name=user.character_name,
            granted_by_character_id=user.character_id,
        )

    # Every registered corp gets a default "90% Jita Buy" config (ADR-0007).
    await config_repo.upsert_config(
        session,
        corporation_id=corp.id,
        market_hub_id=get_settings().market_hub_id,
        default_basis=DEFAULT_BASIS,
        default_percentage=DEFAULT_PERCENTAGE,
        aggregate_field=DEFAULT_AGGREGATE_FIELD,
    )

    await session.commit()
    return corp


async def get_registered_corporation(
    session: AsyncSession, corporation_id: int
) -> CorporationRecord:
    """Fetch the corp by its EVE id (404 if unregistered). The returned record carries
    the internal UUID (`.id`) that corp-scoped child queries use."""
    corp = await corporations_repo.get_by_eve_id(session, corporation_id)
    if corp is None:
        raise CorporationNotRegistered()
    return corp


async def list_managers(
    session: AsyncSession, corporation_id: int
) -> list[ManagerRecord]:
    corp = await get_registered_corporation(session, corporation_id)
    return await managers_repo.list_managers(session, corp.id)


async def add_manager(
    session: AsyncSession,
    esi: EsiClient,
    *,
    corporation_id: int,
    actor_character_id: int,
    target_character_id: int,
) -> ManagerRecord:
    corp = await get_registered_corporation(session, corporation_id)

    target = await esi.get_character(target_character_id)
    if target.corporation_id != corporation_id:
        raise CharacterNotInCorporation()

    char = await characters_repo.upsert_character(
        session, eve_character_id=target_character_id, name=target.name
    )
    if await managers_repo.manager_exists(
        session, corporation_id=corp.id, character_id=char.id
    ):
        raise ManagerAlreadyExists()

    record = await managers_repo.add_manager(
        session,
        corporation_id=corp.id,
        character_id=char.id,
        character_eve_id=target_character_id,
        character_name=target.name,
        granted_by_character_id=actor_character_id,
    )
    await session.commit()
    return record


async def remove_manager(
    session: AsyncSession, *, corporation_id: int, character_id: int
) -> None:
    corp = await get_registered_corporation(session, corporation_id)
    char = await characters_repo.get_by_eve_id(session, character_id)
    removed = char is not None and await managers_repo.remove_manager(
        session, corporation_id=corp.id, character_id=char.id
    )
    if not removed:
        raise ManagerNotFound()
    await session.commit()
