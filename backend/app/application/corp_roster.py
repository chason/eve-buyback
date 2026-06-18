"""Corp-roster fetch + member search for designating Buyback Managers (ADR-0036).

The corp's member list is pulled from ESI using the persisted **Corp ESI access** token
(ADR-0029, broadened) and cached so the designation picker can search real members.
Fetching reuses the stored token **server-side — no EVE round-trip**: on demand (manually,
rate-limited) and via a daily background job. ESI returns the member list only to a
character with the in-game Director role, so the connected character must be a Director
for the roster to populate (`RosterAccessDenied` otherwise — the structure token still
works regardless).
"""

import logging
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.application import structure_tokens as structure_tokens_app
from app.application.corporations import get_registered_corporation
from app.application.errors import RosterAccessDenied, RosterRefreshTooSoon
from app.config import get_settings
from app.data.records import CorpMemberRecord
from app.data.repositories import corp_roster as roster_repo
from app.plugins.esi import CorporationMembersForbidden, EsiClient
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher

log = logging.getLogger(__name__)


class RosterStatus(BaseModel):
    """Whether the corp's roster has been synced, and how fresh it is."""

    synced: bool
    synced_at: datetime | None = None
    member_count: int = 0


async def get_roster_status(
    session: AsyncSession, *, corporation_id: int
) -> RosterStatus:
    """The corp's roster freshness, for the Config panel + Managers page."""
    corp = await get_registered_corporation(session, corporation_id)
    synced_at, count = await roster_repo.roster_status(session, corporation_id=corp.id)
    return RosterStatus(
        synced=synced_at is not None, synced_at=synced_at, member_count=count
    )


async def search_members(
    session: AsyncSession, *, corporation_id: int, query: str
) -> list[CorpMemberRecord]:
    """Search the cached roster by name for the designation typeahead. Empty (rather than
    an error) when the roster has never been synced — the status panel prompts the connect."""
    corp = await get_registered_corporation(session, corporation_id)
    return await roster_repo.search_members(
        session, corporation_id=corp.id, query=query
    )


async def refresh_roster(
    session: AsyncSession,
    sso: EveSsoClient,
    esi: EsiClient,
    *,
    corporation_id: int,
    cipher: TokenCipher,
    now: datetime | None = None,
    enforce_cooldown: bool = True,
) -> RosterStatus:
    """Re-pull the corp's member list with the stored Corp ESI access token and replace
    the cached roster (no EVE round-trip). `enforce_cooldown` (the manual path) rejects a
    refresh within `roster_manual_refresh_min_interval_seconds` of the last sync with
    `RosterRefreshTooSoon`; the daily background job passes `False`. Raises
    `CorpEsiTokenMissing` if no token is connected (via `get_corp_esi_access_token`) and
    `RosterAccessDenied` if the connected character can't read membership (not a Director).
    A members-403 is **not** treated as a token-refresh failure — it leaves the token
    healthy for structure pricing."""
    corp = await get_registered_corporation(session, corporation_id)
    now = now or datetime.now(UTC)

    if enforce_cooldown:
        synced_at, _ = await roster_repo.roster_status(session, corporation_id=corp.id)
        cooldown = get_settings().roster_manual_refresh_min_interval_seconds
        if synced_at is not None and now - synced_at < timedelta(seconds=cooldown):
            raise RosterRefreshTooSoon()

    # Refreshes the access token server-side; raises CorpEsiTokenMissing/Expired.
    access_token = await structure_tokens_app.get_corp_esi_access_token(
        session, sso, corporation_uuid=corp.id, cipher=cipher
    )
    try:
        member_ids = await esi.get_corporation_members(corporation_id, access_token)
    except CorporationMembersForbidden as exc:
        raise RosterAccessDenied() from exc
    names = await esi.resolve_universe_names(member_ids)
    await roster_repo.replace_roster(
        session, corporation_id=corp.id, members=names, synced_at=now
    )
    await session.commit()
    log.info("Refreshed roster for corp %s: %d members", corporation_id, len(names))
    return RosterStatus(synced=True, synced_at=now, member_count=len(names))
