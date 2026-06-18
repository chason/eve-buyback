from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.application import appraisals as appraisals_app
from app.application import open_contract as open_contract_app
from app.application.appraisals import AppraisalItem
from app.interface.deps import SessionDep
from app.interface.security import RequireIdentity, RequireUser, set_session_identity
from app.plugins.cache import Cache, get_cache
from app.plugins.esi import EsiClient, get_esi_client
from app.plugins.esi_market import EsiMarketClient, get_esi_market_client
from app.plugins.fuzzwork import FuzzworkClient, get_fuzzwork_client
from app.plugins.sso import EveSsoClient, get_sso_client
from app.plugins.token_cipher import TokenCipher, get_token_cipher
from app.schemas.appraisal import (
    AppraisalCreateRequest,
    AppraisalOut,
    AppraisalSummaryOut,
)

router = APIRouter(prefix="/appraisals", tags=["appraisals"])

FuzzworkDep = Annotated[FuzzworkClient, Depends(get_fuzzwork_client)]
EsiMarketDep = Annotated[EsiMarketClient, Depends(get_esi_market_client)]
EsiDep = Annotated[EsiClient, Depends(get_esi_client)]
SsoDep = Annotated[EveSsoClient, Depends(get_sso_client)]
CipherDep = Annotated[TokenCipher, Depends(get_token_cipher)]
CacheDep = Annotated[Cache, Depends(get_cache)]


@router.post("", response_model=AppraisalOut, status_code=status.HTTP_201_CREATED)
async def create_appraisal(
    payload: AppraisalCreateRequest,
    user: RequireUser,
    session: SessionDep,
    fuzzwork: FuzzworkDep,
    esi_market: EsiMarketDep,
    sso: SsoDep,
    cipher: CipherDep,
    cache: CacheDep,
) -> AppraisalOut:
    items = [
        AppraisalItem(type_id=i.type_id, quantity=i.quantity) for i in payload.items
    ]
    record = await appraisals_app.create_appraisal(
        session,
        fuzzwork,
        esi_market,
        sso,
        cipher,
        user=user,
        items=items,
        paste=payload.paste,
        delivery_location_id=payload.delivery_location_id,
        cache=cache,
        now=datetime.now(UTC),
    )
    return AppraisalOut(**record.model_dump())


@router.get("", response_model=list[AppraisalSummaryOut])
async def list_appraisals(
    user: RequireUser, session: SessionDep
) -> list[AppraisalSummaryOut]:
    records = await appraisals_app.list_appraisals(session, user=user)
    return [AppraisalSummaryOut(**r.model_dump()) for r in records]


@router.get("/{public_id}", response_model=AppraisalOut)
async def get_appraisal(
    public_id: str, user: RequireUser, session: SessionDep
) -> AppraisalOut:
    record = await appraisals_app.get_appraisal(
        session, corporation_id=user.corporation_id, public_id=public_id
    )
    return AppraisalOut(**record.model_dump())


@router.post("/{public_id}/open-contract", status_code=status.HTTP_204_NO_CONTENT)
async def open_contract(
    public_id: str,
    request: Request,
    identity: RequireIdentity,
    session: SessionDep,
    sso: SsoDep,
    esi: EsiDep,
    cipher: CipherDep,
) -> None:
    """Open the appraisal's matched contract in the caller's own EVE client (ADR-0038),
    using the login refresh token held encrypted in their session."""
    new_token = await open_contract_app.open_matched_contract(
        session,
        sso,
        esi,
        cipher,
        corporation_id=identity.corporation_id,
        public_id=public_id,
        encrypted_login_token=identity.encrypted_login_token,
    )
    # EVE may rotate the refresh token on use — re-seal the cookie with the new one.
    if new_token is not None and new_token != identity.encrypted_login_token:
        set_session_identity(
            request,
            identity.model_copy(update={"encrypted_login_token": new_token}),
        )
