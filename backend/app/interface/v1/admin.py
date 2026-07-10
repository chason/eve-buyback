"""App-admin endpoints (ADR-0041): the instance operator's surface, and the app's one
deliberately cross-tenant router. Every endpoint requires `require_app_admin`; nothing
here is corp-scoped, and nothing corp-scoped belongs here.

Feature access (ADR-0042) is managed per corp by EVE corporation id. The only gated
feature today is the accounting add-on, so the feature key is fixed server-side."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.application import entitlements as entitlements_app
from app.application import operator_wallet as wallet_app
from app.application import payments as payments_app
from app.config import get_settings
from app.data.records import OperatorWalletTokenRecord, WalletPaymentRecord
from app.interface.deps import SessionDep
from app.interface.security import RequireAppAdmin
from app.plugins.sso import EveSsoClient, get_sso_client
from app.plugins.token_cipher import TokenCipher, get_token_cipher
from app.schemas.admin import (
    AccessGrantRequest,
    CorpAccessOut,
    OperatorWalletStatus,
    PaymentMatchRequest,
    PaymentOut,
    WalletAuthorizeRequest,
    WalletAuthorizeResponse,
)

router = APIRouter(prefix="/admin", tags=["admin"])

SsoDep = Annotated[EveSsoClient, Depends(get_sso_client)]
CipherDep = Annotated[TokenCipher, Depends(get_token_cipher)]

# Session keys for the operator-wallet SSO round-trip — distinct from the login and
# corp-ESI flows so grants in flight can't collide.
WALLET_OAUTH_STATE_KEY = "opwallet_oauth_state"
WALLET_PKCE_VERIFIER_KEY = "opwallet_pkce_verifier"

# The single gated feature (ADR-0042). New paid features widen the domain Literal and
# surface their own key here.
_FEATURE = "accounting"


@router.get("/access", response_model=list[CorpAccessOut])
async def list_corp_access(
    user: RequireAppAdmin, session: SessionDep
) -> list[CorpAccessOut]:
    """All registered corps with their accounting-access status (cross-tenant)."""
    access = await entitlements_app.list_corp_access(session, feature=_FEATURE)
    return [CorpAccessOut(**a.model_dump()) for a in access]


@router.put("/access/{corporation_id}", response_model=CorpAccessOut)
async def grant_corp_access(
    corporation_id: int,
    payload: AccessGrantRequest,
    user: RequireAppAdmin,
    session: SessionDep,
) -> CorpAccessOut:
    """Grant or extend a corp's access (`source=admin`); null expiry = perpetual."""
    access = await entitlements_app.grant_access(
        session,
        corporation_eve_id=corporation_id,
        feature=_FEATURE,
        expires_at=payload.expires_at,
        granted_by_character_id=user.character_id,
    )
    return CorpAccessOut(**access.model_dump())


@router.delete("/access/{corporation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_corp_access(
    corporation_id: int, user: RequireAppAdmin, session: SessionDep
) -> None:
    """Revoke a corp's access. Idempotent."""
    await entitlements_app.revoke_access(
        session, corporation_eve_id=corporation_id, feature=_FEATURE
    )


# --- operator wallet (ADR-0042): the payment-reconciliation credential ------------


def _wallet_status(record: OperatorWalletTokenRecord | None) -> OperatorWalletStatus:
    configured = get_settings().corp_esi_token_configured
    if record is None:
        return OperatorWalletStatus(configured=configured, connected=False)
    return OperatorWalletStatus(
        configured=configured,
        connected=True,
        character_name=record.character_name,
        expired=record.last_refresh_failed_at is not None,
        created_at=record.created_at,
    )


@router.get("/wallet", response_model=OperatorWalletStatus)
async def get_wallet_status(
    user: RequireAppAdmin, session: SessionDep
) -> OperatorWalletStatus:
    return _wallet_status(await wallet_app.get_status(session))


@router.post("/wallet/authorize", response_model=WalletAuthorizeResponse)
async def authorize_wallet(
    request: Request, user: RequireAppAdmin, sso: SsoDep
) -> WalletAuthorizeResponse:
    """Begin the operator wallet grant: mint state + PKCE, return the SSO URL."""
    challenge = wallet_app.begin_wallet_authorize(sso)
    request.session[WALLET_OAUTH_STATE_KEY] = challenge.state
    request.session[WALLET_PKCE_VERIFIER_KEY] = challenge.verifier
    return WalletAuthorizeResponse(
        authorization_url=challenge.authorization_url, state=challenge.state
    )


@router.post("/wallet/session", response_model=OperatorWalletStatus)
async def complete_wallet(
    payload: WalletAuthorizeRequest,
    request: Request,
    user: RequireAppAdmin,
    session: SessionDep,
    sso: SsoDep,
    cipher: CipherDep,
) -> OperatorWalletStatus:
    """Complete the grant: validate state, exchange the code, store the token."""
    expected_state = request.session.get(WALLET_OAUTH_STATE_KEY)
    verifier = request.session.get(WALLET_PKCE_VERIFIER_KEY)
    if not expected_state or not verifier or payload.state != expected_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state",
        )
    record = await wallet_app.complete_wallet_authorize(
        session, sso, code=payload.code, verifier=verifier, cipher=cipher
    )
    request.session.pop(WALLET_OAUTH_STATE_KEY, None)
    request.session.pop(WALLET_PKCE_VERIFIER_KEY, None)
    return _wallet_status(record)


@router.delete("/wallet", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_wallet(
    user: RequireAppAdmin, session: SessionDep, sso: SsoDep, cipher: CipherDep
) -> None:
    await wallet_app.revoke(session, sso, cipher=cipher)


# --- payments (ADR-0042): the reconciliation audit list + manual match ------------


def _payment_out(record: WalletPaymentRecord) -> PaymentOut:
    return PaymentOut(
        id=record.id,
        amount=str(record.amount),
        sender_name=record.sender_name,
        reason=record.reason,
        received_at=record.received_at,
        matched=record.matched_corporation_id is not None,
        matched_corporation_name=record.matched_corporation_name,
        periods_granted=record.periods_granted,
    )


@router.get("/payments", response_model=list[PaymentOut])
async def list_payments(
    user: RequireAppAdmin, session: SessionDep, unmatched: bool = False
) -> list[PaymentOut]:
    """Recent incoming payments (newest first); `?unmatched=true` filters to the ones
    awaiting a manual match."""
    records = await payments_app.list_payments(session, unmatched_only=unmatched)
    return [_payment_out(r) for r in records]


@router.post("/payments/{payment_id}/match", response_model=PaymentOut)
async def match_payment(
    payment_id: uuid.UUID,
    payload: PaymentMatchRequest,
    user: RequireAppAdmin,
    session: SessionDep,
) -> PaymentOut:
    """Apply an unmatched payment to a corp (extends their access by the periods the
    amount covers)."""
    record = await payments_app.match_payment(
        session,
        payment_id=payment_id,
        corporation_eve_id=payload.corporation_id,
        matched_by_character_id=user.character_id,
    )
    return _payment_out(record)
