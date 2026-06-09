"""Accepted buyback drop-off location use cases (ADR-0030). Manager-gating is
enforced at the interface; these own existence/validation rules and the commit.

A location is **where members deliver** bought-back items — independent of the pricing
hub. An NPC station is validated against the seeded SDE (its name/system are resolved
server-side); a player structure has no SDE, so its name comes from the caller (the
structure search already named it), and only its numeric id is validated — the same
posture as the structure hub (`pricing._resolve_hub`).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.corporations import get_registered_corporation
from app.application.errors import LocationInvalid, LocationNotFound
from app.data.records import BuybackLocationRecord
from app.data.repositories import buyback_locations as locations_repo
from app.data.repositories import sde as sde_repo
from app.domain.locations import LocationKind, is_valid_location_id


async def list_locations(
    session: AsyncSession, corporation_id: int
) -> list[BuybackLocationRecord]:
    """The corp's accepted drop-off locations (any member may read — they pick one
    when appraising)."""
    corp = await get_registered_corporation(session, corporation_id)
    return await locations_repo.list_for_corp(session, corp.id)


async def add_location(
    session: AsyncSession,
    corporation_id: int,
    *,
    kind: LocationKind,
    location_id: str,
    name: str | None = None,
) -> BuybackLocationRecord:
    """Add an accepted drop-off location (manager-gated at the interface). Idempotent —
    re-adding an existing location returns it unchanged."""
    corp = await get_registered_corporation(session, corporation_id)
    if not is_valid_location_id(location_id):
        raise LocationInvalid(f"Invalid location id {location_id!r}")

    if kind == "npc_station":
        # Validate against the SDE and cache the authoritative name + system.
        station = await sde_repo.get_station(session, int(location_id))
        if station is None:
            raise LocationInvalid(f"Unknown NPC station {location_id}")
        resolved_name = f"{station.system_name} - {station.name}"
        system_name = station.system_name
    else:  # structure — no SDE; trust the name from the structure search.
        if not name:
            raise LocationInvalid("A structure location requires a name")
        resolved_name = name
        system_name = None

    record = await locations_repo.add(
        session,
        corp.id,
        kind=kind,
        location_id=location_id,
        name=resolved_name,
        system_name=system_name,
    )
    await session.commit()
    return record


async def remove_location(
    session: AsyncSession, corporation_id: int, *, location_id: str
) -> None:
    corp = await get_registered_corporation(session, corporation_id)
    removed = await locations_repo.delete_for_corp(session, corp.id, location_id)
    if not removed:
        raise LocationNotFound()
    await session.commit()
