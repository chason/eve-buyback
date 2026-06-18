"""Corp-roster sync + member search for designating Buyback Managers (ADR-0036).

A CEO or Director syncs the corporation's member list via a separate SSO round-trip
(no EVE token is persisted); the synced roster powers the manager-designation search.
The OAuth state/PKCE are stashed under their own session keys so they can't collide with
a login or a structure grant in flight."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.application import corp_roster as roster_app
from app.interface.deps import SessionDep
from app.interface.security import RequireCeoOrDirector
from app.plugins.esi import EsiClient, get_esi_client
from app.plugins.sso import EveSsoClient, get_sso_client
from app.schemas.roster import (
    CorpMemberOut,
    RosterStatusOut,
    RosterSyncRequest,
    RosterSyncResponse,
)

router = APIRouter(prefix="/corporations/me/roster", tags=["roster"])

SsoDep = Annotated[EveSsoClient, Depends(get_sso_client)]
EsiDep = Annotated[EsiClient, Depends(get_esi_client)]

ROSTER_OAUTH_STATE_KEY = "roster_oauth_state"
ROSTER_PKCE_VERIFIER_KEY = "roster_pkce_verifier"


@router.get("", response_model=RosterStatusOut)
async def get_status(
    user: RequireCeoOrDirector, session: SessionDep
) -> RosterStatusOut:
    result = await roster_app.get_roster_status(
        session, corporation_id=user.corporation_id
    )
    return RosterStatusOut(**result.model_dump())


@router.post("/sync", response_model=RosterSyncResponse)
async def begin_sync(
    user: RequireCeoOrDirector, request: Request, sso: SsoDep
) -> RosterSyncResponse:
    """Begin a roster sync: mint state + PKCE and return the SSO URL to redirect to."""
    challenge = roster_app.begin_roster_sync(sso)
    request.session[ROSTER_OAUTH_STATE_KEY] = challenge.state
    request.session[ROSTER_PKCE_VERIFIER_KEY] = challenge.verifier
    return RosterSyncResponse(
        authorization_url=challenge.authorization_url, state=challenge.state
    )


@router.post("/sync/session", response_model=RosterStatusOut)
async def complete_sync(
    payload: RosterSyncRequest,
    user: RequireCeoOrDirector,
    request: Request,
    session: SessionDep,
    sso: SsoDep,
    esi: EsiDep,
) -> RosterStatusOut:
    """Complete a roster sync: validate state, exchange the code, cache the members."""
    expected_state = request.session.get(ROSTER_OAUTH_STATE_KEY)
    verifier = request.session.get(ROSTER_PKCE_VERIFIER_KEY)
    if not expected_state or not verifier or payload.state != expected_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state",
        )
    result = await roster_app.complete_roster_sync(
        session, sso, esi, user=user, code=payload.code, verifier=verifier
    )
    request.session.pop(ROSTER_OAUTH_STATE_KEY, None)
    request.session.pop(ROSTER_PKCE_VERIFIER_KEY, None)
    return RosterStatusOut(**result.model_dump())


@router.get("/members", response_model=list[CorpMemberOut])
async def search_members(
    user: RequireCeoOrDirector,
    session: SessionDep,
    q: Annotated[str, Query(min_length=2)],
) -> list[CorpMemberOut]:
    """Search the synced roster by name (requires a prior sync; empty until then)."""
    members = await roster_app.search_members(
        session, corporation_id=user.corporation_id, query=q
    )
    return [CorpMemberOut(character_id=m.character_id, name=m.name) for m in members]
