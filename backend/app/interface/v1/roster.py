"""Corp-roster status, refresh, and member search for designating Buyback Managers
(ADR-0036). The roster is pulled server-side with the persisted Corp ESI access token
(connected on the Config page), so there's no SSO round-trip here. CEO/Director only —
designating managers is their job."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.application import corp_roster as roster_app
from app.interface.deps import SessionDep
from app.interface.security import RequireCeoOrDirector
from app.plugins.esi import EsiClient, get_esi_client
from app.plugins.sso import EveSsoClient, get_sso_client
from app.plugins.token_cipher import TokenCipher, get_token_cipher
from app.schemas.roster import CorpMemberOut, RosterStatusOut

router = APIRouter(prefix="/corporations/me/roster", tags=["roster"])

SsoDep = Annotated[EveSsoClient, Depends(get_sso_client)]
EsiDep = Annotated[EsiClient, Depends(get_esi_client)]
CipherDep = Annotated[TokenCipher, Depends(get_token_cipher)]


@router.get("", response_model=RosterStatusOut)
async def get_status(
    user: RequireCeoOrDirector, session: SessionDep
) -> RosterStatusOut:
    result = await roster_app.get_roster_status(
        session, corporation_id=user.corporation_id
    )
    return RosterStatusOut(**result.model_dump())


@router.post("/refresh", response_model=RosterStatusOut)
async def refresh(
    user: RequireCeoOrDirector,
    session: SessionDep,
    sso: SsoDep,
    esi: EsiDep,
    cipher: CipherDep,
) -> RosterStatusOut:
    """Manually re-pull the roster with the stored token (rate-limited; 429 if too soon)."""
    result = await roster_app.refresh_roster(
        session,
        sso,
        esi,
        corporation_id=user.corporation_id,
        cipher=cipher,
        enforce_cooldown=True,
    )
    return RosterStatusOut(**result.model_dump())


@router.get("/members", response_model=list[CorpMemberOut])
async def search_members(
    user: RequireCeoOrDirector,
    session: SessionDep,
    q: Annotated[str, Query(min_length=2)],
) -> list[CorpMemberOut]:
    """Search the synced roster by name (empty until the corp roster has been synced)."""
    members = await roster_app.search_members(
        session, corporation_id=user.corporation_id, query=q
    )
    return [CorpMemberOut(character_id=m.character_id, name=m.name) for m in members]
