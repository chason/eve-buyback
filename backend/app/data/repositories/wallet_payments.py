"""Wallet-payment persistence (ADR-0042): the audit trail of incoming ISK transfers
seen in the operator's journal, keyed by EVE's `journal_id` so re-polling is
idempotent. Repositories return records and never commit (data/ conventions)."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Corporation, WalletPayment
from app.data.records import WalletPaymentRecord


async def existing_journal_ids(
    session: AsyncSession, journal_ids: list[int]
) -> set[int]:
    """Which of these journal entries are already recorded (idempotent re-poll)."""
    if not journal_ids:
        return set()
    rows = await session.execute(
        select(WalletPayment.journal_id).where(WalletPayment.journal_id.in_(journal_ids))
    )
    return {jid for (jid,) in rows}


async def add(
    session: AsyncSession,
    *,
    journal_id: int,
    amount: Decimal,
    sender_eve_id: int | None,
    sender_name: str | None,
    reason: str | None,
    received_at: datetime,
    matched_corporation_id: uuid.UUID | None = None,
    periods_granted: int = 0,
    matched_at: datetime | None = None,
) -> WalletPaymentRecord:
    row = WalletPayment(
        journal_id=journal_id,
        amount=amount,
        sender_eve_id=sender_eve_id,
        sender_name=sender_name,
        reason=reason,
        received_at=received_at,
        matched_corporation_id=matched_corporation_id,
        periods_granted=periods_granted,
        matched_at=matched_at,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return WalletPaymentRecord.model_validate(row)


async def list_payments(
    session: AsyncSession, *, unmatched_only: bool = False, limit: int = 100
) -> list[WalletPaymentRecord]:
    """Recent payments, newest first, with the matched corp's name join-derived."""
    stmt = (
        select(WalletPayment, Corporation.name)
        .join(Corporation, Corporation.id == WalletPayment.matched_corporation_id, isouter=True)
        .order_by(WalletPayment.received_at.desc())
        .limit(limit)
    )
    if unmatched_only:
        stmt = stmt.where(WalletPayment.matched_corporation_id.is_(None))
    rows = (await session.execute(stmt)).all()
    return [
        WalletPaymentRecord.model_validate(payment).model_copy(
            update={"matched_corporation_name": corp_name}
        )
        for payment, corp_name in rows
    ]


async def get(session: AsyncSession, payment_id: uuid.UUID) -> WalletPaymentRecord | None:
    stmt = (
        select(WalletPayment, Corporation.name)
        .join(Corporation, Corporation.id == WalletPayment.matched_corporation_id, isouter=True)
        .where(WalletPayment.id == payment_id)
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        return None
    payment, corp_name = row
    return WalletPaymentRecord.model_validate(payment).model_copy(
        update={"matched_corporation_name": corp_name}
    )


async def set_match(
    session: AsyncSession,
    *,
    payment_id: uuid.UUID,
    corporation_id: uuid.UUID,
    periods_granted: int,
    matched_at: datetime,
    matched_by_character_id: int | None,
) -> None:
    row = (
        await session.execute(select(WalletPayment).where(WalletPayment.id == payment_id))
    ).scalar_one()
    row.matched_corporation_id = corporation_id
    row.periods_granted = periods_granted
    row.matched_at = matched_at
    row.matched_by_character_id = matched_by_character_id
    await session.flush()
