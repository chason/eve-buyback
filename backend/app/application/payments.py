"""ISK access-payment reconciliation (ADR-0042).

The background job's use case: read the operator's wallet journal, record every
incoming transfer once (keyed by EVE's journal id), and extend a corp's accounting
access when the transfer carries their payment reference and covers at least one
period. Unmatched payments are kept for the admin to apply by hand — matching is
best-effort by design (typos, missing references, underpayments), so the manual path
is first-class, not a fallback.

Also the checkout read: what a manager sees to pay (price, reference, where to send).
"""

import logging
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.application import operator_wallet as wallet_app
from app.application.errors import (
    CorporationNotRegistered,
    OperatorWalletExpired,
    OperatorWalletMissing,
    PaymentAlreadyMatched,
    PaymentNotFound,
    PaymentTooSmall,
    StructureEncryptionNotConfigured,
)
from app.config import get_settings
from app.data.records import WalletPaymentRecord
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import wallet_payments as payments_repo
from app.domain.entitlements import Feature, entitlement_active
from app.domain.payments import (
    extend_expiry,
    is_incoming_payment,
    parse_payment_reference,
    payment_reference,
    periods_for,
)
from app.plugins.esi import CharacterWalletForbidden, EsiClient
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher

log = logging.getLogger(__name__)

_FEATURE: Feature = "accounting"


class CheckoutInfo(BaseModel):
    """What a manager needs in order to pay for access (ADR-0042): the price, their
    corp's reference, and where the ISK goes. `payment_configured` is False until the
    operator connects a wallet — the UI then shows only the access status."""

    active: bool
    expires_at: datetime | None = None
    price_isk: int
    period_days: int
    reference: str
    payment_configured: bool
    operator_character_name: str | None = None


async def reconcile_payments(
    session: AsyncSession,
    sso: EveSsoClient,
    esi: EsiClient,
    *,
    cipher: TokenCipher,
    now: datetime | None = None,
) -> int:
    """One reconciliation pass. Returns how many new payments were recorded. No
    operator wallet connected → quiet no-op (the feature simply isn't turned on);
    a revoked/scope-less token is flagged on the wallet status and skipped."""
    now = now or datetime.now(UTC)
    settings = get_settings()

    try:
        operator_character_id, access_token = await wallet_app.get_wallet_access_token(
            session, sso, cipher=cipher
        )
    except (OperatorWalletMissing, StructureEncryptionNotConfigured):
        return 0
    except OperatorWalletExpired:
        log.warning("operator wallet grant expired; payment reconciliation skipped")
        return 0

    try:
        journal = await esi.get_character_wallet_journal(
            operator_character_id, access_token
        )
    except CharacterWalletForbidden:
        log.warning("operator wallet scope missing; payment reconciliation skipped")
        return 0

    incoming = [
        e
        for e in journal
        if is_incoming_payment(
            ref_type=e.ref_type,
            amount=e.amount,
            second_party_id=e.second_party_id,
            operator_character_id=operator_character_id,
        )
    ]
    seen = await payments_repo.existing_journal_ids(session, [e.id for e in incoming])
    fresh = [e for e in incoming if e.id not in seen]
    if not fresh:
        return 0

    # Best-effort sender names (characters and corps) for the admin payment list.
    sender_ids = list({e.first_party_id for e in fresh if e.first_party_id})
    try:
        names = await esi.resolve_universe_names(
            sender_ids, categories=("character", "corporation")
        )
    except Exception as exc:  # noqa: BLE001 — names are cosmetic; never fail the pass
        log.warning("resolving payment sender names failed: %r", exc)
        names = {}

    recorded = 0
    for entry in fresh:
        corp_eve_id = parse_payment_reference(entry.reason)
        corp = (
            await corporations_repo.get_by_eve_id(session, corp_eve_id)
            if corp_eve_id is not None
            else None
        )
        periods = periods_for(entry.amount, settings.accounting_price_isk)
        matched = corp is not None and periods > 0
        if matched:
            await _extend_access(session, corp.id, periods=periods, now=now)
        await payments_repo.add(
            session,
            journal_id=entry.id,
            amount=entry.amount,
            sender_eve_id=entry.first_party_id,
            sender_name=names.get(entry.first_party_id or 0),
            reason=entry.reason,
            received_at=entry.date,
            matched_corporation_id=corp.id if matched else None,
            periods_granted=periods if matched else 0,
            matched_at=now if matched else None,
        )
        recorded += 1
        if matched:
            log.info(
                "payment %s matched: %s ISK -> corp %s (+%d period(s))",
                entry.id, entry.amount, corp_eve_id, periods,
            )
    await session.commit()
    return recorded


async def list_payments(
    session: AsyncSession, *, unmatched_only: bool = False
) -> list[WalletPaymentRecord]:
    """Recent payments for the admin list (newest first)."""
    return await payments_repo.list_payments(session, unmatched_only=unmatched_only)


async def match_payment(
    session: AsyncSession,
    *,
    payment_id: uuid.UUID,
    corporation_eve_id: int,
    matched_by_character_id: int,
    now: datetime | None = None,
) -> WalletPaymentRecord:
    """Admin action: apply an unmatched payment to a corp. The amount must cover at
    least one period (an underpayment is refused — the admin can use a plain access
    grant instead, which needs no payment)."""
    now = now or datetime.now(UTC)
    payment = await payments_repo.get(session, payment_id)
    if payment is None:
        raise PaymentNotFound()
    if payment.matched_corporation_id is not None:
        raise PaymentAlreadyMatched()
    corp = await corporations_repo.get_by_eve_id(session, corporation_eve_id)
    if corp is None:
        raise CorporationNotRegistered()
    periods = periods_for(payment.amount, get_settings().accounting_price_isk)
    if periods < 1:
        raise PaymentTooSmall()
    await _extend_access(session, corp.id, periods=periods, now=now)
    await payments_repo.set_match(
        session,
        payment_id=payment_id,
        corporation_id=corp.id,
        periods_granted=periods,
        matched_at=now,
        matched_by_character_id=matched_by_character_id,
    )
    await session.commit()
    record = await payments_repo.get(session, payment_id)
    assert record is not None
    return record


async def checkout_info(
    session: AsyncSession, *, corporation_eve_id: int, now: datetime | None = None
) -> CheckoutInfo:
    """What the corp's manager sees on the access panel: current status plus, when the
    operator wallet is connected, how to pay."""
    now = now or datetime.now(UTC)
    settings = get_settings()
    corp = await corporations_repo.get_by_eve_id(session, corporation_eve_id)
    if corp is None:
        raise CorporationNotRegistered()
    entitlement = await entitlements_repo.get(
        session, corporation_id=corp.id, feature=_FEATURE
    )
    wallet = await wallet_app.get_status(session)
    return CheckoutInfo(
        active=(
            entitlement is not None and entitlement_active(entitlement.expires_at, now)
        ),
        expires_at=entitlement.expires_at if entitlement else None,
        price_isk=settings.accounting_price_isk,
        period_days=settings.accounting_period_days,
        reference=payment_reference(corporation_eve_id),
        payment_configured=wallet is not None,
        operator_character_name=wallet.character_name if wallet else None,
    )


async def _extend_access(
    session: AsyncSession, corporation_id: uuid.UUID, *, periods: int, now: datetime
) -> None:
    """Extend the corp's accounting access by `periods` (source=payment, ADR-0042):
    stacked onto remaining time, restarted when lapsed. A perpetual admin grant (NULL
    expiry) is left untouched — there is nothing to extend."""
    existing = await entitlements_repo.get(
        session, corporation_id=corporation_id, feature=_FEATURE
    )
    if existing is not None and existing.expires_at is None:
        log.info("corp %s has perpetual access; payment recorded, nothing to extend",
                 corporation_id)
        return
    new_expiry = extend_expiry(
        existing.expires_at if existing else None,
        now=now,
        periods=periods,
        period_days=get_settings().accounting_period_days,
    )
    await entitlements_repo.upsert(
        session,
        corporation_id=corporation_id,
        feature=_FEATURE,
        source="payment",
        expires_at=new_expiry,
        granted_by_character_id=None,
    )
