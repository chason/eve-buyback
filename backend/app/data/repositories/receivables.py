"""Receivable persistence (ADR-0045, #158): append + clear, never edit or delete.
The application owns the commit."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Receivable
from app.data.records import ReceivableRecord


async def create(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    amount: Decimal,
    note: str,
    incurred_at: datetime,
    entered_by_character_id: int,
) -> ReceivableRecord:
    row = Receivable(
        corporation_id=corporation_id,
        amount=amount,
        note=note,
        incurred_at=incurred_at,
        entered_by_character_id=entered_by_character_id,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return ReceivableRecord.model_validate(row)


async def get_for_corp(
    session: AsyncSession, *, corporation_id: uuid.UUID, receivable_id: uuid.UUID
) -> ReceivableRecord | None:
    row = (
        await session.execute(
            select(Receivable).where(
                Receivable.id == receivable_id,
                Receivable.corporation_id == corporation_id,
            )
        )
    ).scalar_one_or_none()
    return ReceivableRecord.model_validate(row) if row else None


async def list_for_corp(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> list[ReceivableRecord]:
    """All receivables, open first, newest first within each group."""
    rows = (
        (
            await session.execute(
                select(Receivable)
                .where(Receivable.corporation_id == corporation_id)
                .order_by(
                    Receivable.cleared_at.is_not(None),
                    Receivable.incurred_at.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return [ReceivableRecord.model_validate(row) for row in rows]


async def clear(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    receivable_id: uuid.UUID,
    cleared_at: datetime,
    cleared_by_character_id: int,
    cleared_note: str | None,
) -> ReceivableRecord | None:
    """Mark it paid. None when it doesn't exist (or is another corp's); already-
    cleared rows are returned unchanged (idempotent — the end state holds)."""
    row = (
        await session.execute(
            select(Receivable).where(
                Receivable.id == receivable_id,
                Receivable.corporation_id == corporation_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.cleared_at is None:
        row.cleared_at = cleared_at
        row.cleared_by_character_id = cleared_by_character_id
        row.cleared_note = cleared_note
        await session.flush()
    return ReceivableRecord.model_validate(row)
