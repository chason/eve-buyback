"""Corp-roster sync + member search for manager designation (ADR-0036).

A separate, CEO/Director-gated SSO step-up pulls the corporation's member list from ESI
and caches it (id + name) so the manager-designation picker can search real members. No
EVE token is persisted: the access token is used once during the sync and discarded; the
roster is re-synced on demand. The membership scope stays off normal login (ADR-0004), so
ordinary members never consent to it.
"""

import logging
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.auth import AuthenticatedUser, LoginChallenge
from app.application.corporations import get_registered_corporation
from app.application.errors import RosterAccessDenied, SsoNotConfigured
from app.config import get_settings
from app.data.records import CorpMemberRecord
from app.data.repositories import corp_roster as roster_repo
from app.domain import auth as auth_rules
from app.plugins.esi import CorporationMembersForbidden, EsiClient
from app.plugins.sso import EveSsoClient

log = logging.getLogger(__name__)

# Self-identifying OAuth state prefix so the shared SSO callback routes a roster sync to
# the right completion (alongside login and the structure flow, ADR-0029). The state is
# echoed back by EVE in the redirect; login states never contain ".", so it's unambiguous.
ROSTER_STATE_PREFIX = "roster."


class RosterStatus(BaseModel):
    """Whether the corp's roster has been synced, and how fresh it is."""

    synced: bool
    synced_at: datetime | None = None
    member_count: int = 0


def begin_roster_sync(sso: EveSsoClient) -> LoginChallenge:
    """Mint the PKCE/state challenge for the roster-membership step-up."""
    if not sso.configured:
        raise SsoNotConfigured()
    state = ROSTER_STATE_PREFIX + auth_rules.generate_state()
    verifier, challenge = auth_rules.generate_pkce()
    url = sso.build_authorize_url(
        state=state,
        code_challenge=challenge,
        scopes=get_settings().eve_roster_scopes,
    )
    return LoginChallenge(authorization_url=url, state=state, verifier=verifier)


async def complete_roster_sync(
    session: AsyncSession,
    sso: EveSsoClient,
    esi: EsiClient,
    *,
    user: AuthenticatedUser,
    code: str,
    verifier: str,
    now: datetime | None = None,
) -> RosterStatus:
    """Exchange the code, pull the corp member list with the (transient) access token,
    resolve names, and cache the roster — replacing any prior snapshot. The token is not
    persisted. Authorized for the CEO or a Director (enforced at the interface)."""
    corp = await get_registered_corporation(session, user.corporation_id)
    token = await sso.exchange_code(code, verifier)
    try:
        member_ids = await esi.get_corporation_members(
            user.corporation_id, token.access_token
        )
    except CorporationMembersForbidden as exc:
        raise RosterAccessDenied() from exc
    names = await esi.resolve_universe_names(member_ids)
    synced_at = now or datetime.now(UTC)
    await roster_repo.replace_roster(
        session, corporation_id=corp.id, members=names, synced_at=synced_at
    )
    await session.commit()
    log.info(
        "Synced roster for corp %s: %d members", user.corporation_id, len(names)
    )
    return RosterStatus(synced=True, synced_at=synced_at, member_count=len(names))


async def get_roster_status(
    session: AsyncSession, *, corporation_id: int
) -> RosterStatus:
    """The corp's roster freshness, for the Managers page status panel."""
    corp = await get_registered_corporation(session, corporation_id)
    synced_at, count = await roster_repo.roster_status(session, corporation_id=corp.id)
    return RosterStatus(
        synced=synced_at is not None, synced_at=synced_at, member_count=count
    )


async def search_members(
    session: AsyncSession, *, corporation_id: int, query: str
) -> list[CorpMemberRecord]:
    """Search the cached roster by name for the designation typeahead. Empty (rather than
    an error) when the roster has never been synced — the status panel prompts the sync."""
    corp = await get_registered_corporation(session, corporation_id)
    return await roster_repo.search_members(
        session, corporation_id=corp.id, query=query
    )
