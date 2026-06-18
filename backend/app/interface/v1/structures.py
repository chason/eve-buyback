"""Corp ESI access endpoints (ADR-0029, ADR-0036). A CEO/Director connects (or revokes)
the corp's one encrypted EVE token — covering both structure-market reads and corp-roster
membership — via a separate SSO round-trip; the OAuth state/PKCE are stashed under their
own session keys so they can't collide with a login in flight. Status + structure-name
search stay manager-visible (managers configure structure hubs)."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.application import corp_roster as roster_app
from app.application import structure_tokens as structures_app
from app.application.auth import AuthenticatedUser
from app.config import get_settings
from app.data.records import StructureMarketTokenRecord
from app.interface.deps import SessionDep
from app.interface.security import RequireCeoOrDirector, require_role
from app.plugins.esi import EsiClient, get_esi_client
from app.plugins.esi_market import EsiMarketClient, get_esi_market_client
from app.plugins.sso import EveSsoClient, get_sso_client
from app.plugins.token_cipher import TokenCipher, get_token_cipher
from app.schemas.structures import (
    StructureAuthorizeRequest,
    StructureAuthorizeResponse,
    StructureSearchResult,
    StructureTokenStatus,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/corporations/me/structure-token", tags=["structures"])

# Reads stay manager-visible; connect/revoke are CEO/Director (ADR-0036).
ManagerUser = Annotated[AuthenticatedUser, Depends(require_role("manager"))]
SsoDep = Annotated[EveSsoClient, Depends(get_sso_client)]
CipherDep = Annotated[TokenCipher, Depends(get_token_cipher)]
EsiDep = Annotated[EsiClient, Depends(get_esi_client)]
EsiMarketDep = Annotated[EsiMarketClient, Depends(get_esi_market_client)]

STRUCT_OAUTH_STATE_KEY = "struct_oauth_state"
STRUCT_PKCE_VERIFIER_KEY = "struct_pkce_verifier"


def _status(
    record: StructureMarketTokenRecord | None,
    *,
    replaced_character_name: str | None = None,
) -> StructureTokenStatus:
    configured = get_settings().corp_esi_token_configured
    if record is None:
        return StructureTokenStatus(configured=configured, authorized=False)
    return StructureTokenStatus(
        configured=configured,
        authorized=True,
        character_name=record.character_name,
        scopes=record.scopes,
        expired=record.last_refresh_failed_at is not None,
        failed_since=record.last_refresh_failed_at,
        created_at=record.created_at,
        replaced_character_name=replaced_character_name,
    )


@router.get("", response_model=StructureTokenStatus)
async def get_status(user: ManagerUser, session: SessionDep) -> StructureTokenStatus:
    record = await structures_app.get_status(session, corporation_id=user.corporation_id)
    return _status(record)


@router.get("/search", response_model=list[StructureSearchResult])
async def search(
    user: ManagerUser,
    session: SessionDep,
    sso: SsoDep,
    esi_market: EsiMarketDep,
    cipher: CipherDep,
    q: Annotated[str, Query(min_length=3)],
) -> list[StructureSearchResult]:
    """Search the corp's accessible structures by name (requires prior authorization)."""
    results = await structures_app.search_structures(
        session, sso, esi_market, corporation_id=user.corporation_id, query=q, cipher=cipher
    )
    return [
        StructureSearchResult(structure_id=r.structure_id, name=r.name) for r in results
    ]


@router.post("/authorize", response_model=StructureAuthorizeResponse)
async def authorize(
    request: Request, user: RequireCeoOrDirector, sso: SsoDep
) -> StructureAuthorizeResponse:
    """Begin the corp ESI access grant: mint state + PKCE and return the SSO URL."""
    challenge = structures_app.begin_corp_esi_authorize(sso)
    request.session[STRUCT_OAUTH_STATE_KEY] = challenge.state
    request.session[STRUCT_PKCE_VERIFIER_KEY] = challenge.verifier
    return StructureAuthorizeResponse(
        authorization_url=challenge.authorization_url, state=challenge.state
    )


@router.post("/session", response_model=StructureTokenStatus)
async def complete(
    payload: StructureAuthorizeRequest,
    request: Request,
    user: RequireCeoOrDirector,
    session: SessionDep,
    sso: SsoDep,
    esi: EsiDep,
    cipher: CipherDep,
) -> StructureTokenStatus:
    """Complete the grant: validate state, exchange the code, store the token, and
    best-effort populate the roster (works if the connected character is a Director)."""
    expected_state = request.session.get(STRUCT_OAUTH_STATE_KEY)
    verifier = request.session.get(STRUCT_PKCE_VERIFIER_KEY)
    if not expected_state or not verifier or payload.state != expected_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state",
        )
    result = await structures_app.complete_corp_esi_authorize(
        session, sso, esi, code=payload.code, verifier=verifier, user=user, cipher=cipher
    )
    request.session.pop(STRUCT_OAUTH_STATE_KEY, None)
    request.session.pop(STRUCT_PKCE_VERIFIER_KEY, None)
    # Fill the roster immediately if the connected character can read membership. This is
    # best-effort: a non-Director still gets a working structure token (roster stays empty).
    try:
        await roster_app.refresh_roster(
            session,
            sso,
            esi,
            corporation_id=user.corporation_id,
            cipher=cipher,
            enforce_cooldown=False,
        )
    except Exception:  # noqa: BLE001 — the connect already succeeded; roster is a bonus
        log.info("roster auto-populate after connect failed (character may not be a Director)")
    return _status(
        result.token, replaced_character_name=result.replaced_character_name
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def revoke(
    user: RequireCeoOrDirector, session: SessionDep, sso: SsoDep, cipher: CipherDep
) -> None:
    await structures_app.revoke(
        session, sso, corporation_id=user.corporation_id, cipher=cipher
    )
