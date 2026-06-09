"""Structure-market authorization endpoints (ADR-0029). A manager connects (or
revokes) the corp's encrypted structure-access token via a separate SSO round-trip;
the OAuth state/PKCE are stashed under their own session keys so they can't collide
with a login in flight."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.application import structure_tokens as structures_app
from app.application.auth import AuthenticatedUser
from app.data.records import StructureMarketTokenRecord
from app.interface.deps import SessionDep
from app.interface.security import require_role
from app.plugins.esi_market import EsiMarketClient, get_esi_market_client
from app.plugins.sso import EveSsoClient, get_sso_client
from app.plugins.token_cipher import TokenCipher, get_token_cipher
from app.schemas.structures import (
    StructureAuthorizeRequest,
    StructureAuthorizeResponse,
    StructureSearchResult,
    StructureTokenStatus,
)

router = APIRouter(prefix="/corporations/me/structure-token", tags=["structures"])

ManagerUser = Annotated[AuthenticatedUser, Depends(require_role("manager"))]
SsoDep = Annotated[EveSsoClient, Depends(get_sso_client)]
CipherDep = Annotated[TokenCipher, Depends(get_token_cipher)]
EsiMarketDep = Annotated[EsiMarketClient, Depends(get_esi_market_client)]

STRUCT_OAUTH_STATE_KEY = "struct_oauth_state"
STRUCT_PKCE_VERIFIER_KEY = "struct_pkce_verifier"


def _status(
    record: StructureMarketTokenRecord | None,
    *,
    replaced_character_name: str | None = None,
) -> StructureTokenStatus:
    if record is None:
        return StructureTokenStatus(authorized=False)
    return StructureTokenStatus(
        authorized=True,
        character_name=record.character_name,
        scopes=record.scopes,
        expired=record.last_refresh_failed_at is not None,
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
    return [StructureSearchResult(**r) for r in results]


@router.post("/authorize", response_model=StructureAuthorizeResponse)
async def authorize(
    request: Request, user: ManagerUser, sso: SsoDep
) -> StructureAuthorizeResponse:
    """Begin the structure-access grant: mint state + PKCE and return the SSO URL."""
    challenge = structures_app.begin_structure_authorize(sso)
    request.session[STRUCT_OAUTH_STATE_KEY] = challenge.state
    request.session[STRUCT_PKCE_VERIFIER_KEY] = challenge.verifier
    return StructureAuthorizeResponse(
        authorization_url=challenge.authorization_url, state=challenge.state
    )


@router.post("/session", response_model=StructureTokenStatus)
async def complete(
    payload: StructureAuthorizeRequest,
    request: Request,
    user: ManagerUser,
    session: SessionDep,
    sso: SsoDep,
    cipher: CipherDep,
) -> StructureTokenStatus:
    """Complete the grant: validate state, exchange the code, store the token."""
    expected_state = request.session.get(STRUCT_OAUTH_STATE_KEY)
    verifier = request.session.get(STRUCT_PKCE_VERIFIER_KEY)
    if not expected_state or not verifier or payload.state != expected_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state",
        )
    result = await structures_app.complete_structure_authorize(
        session, sso, code=payload.code, verifier=verifier, user=user, cipher=cipher
    )
    request.session.pop(STRUCT_OAUTH_STATE_KEY, None)
    request.session.pop(STRUCT_PKCE_VERIFIER_KEY, None)
    return _status(
        result.token, replaced_character_name=result.replaced_character_name
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def revoke(
    user: ManagerUser, session: SessionDep, sso: SsoDep, cipher: CipherDep
) -> None:
    await structures_app.revoke(
        session, sso, corporation_id=user.corporation_id, cipher=cipher
    )
