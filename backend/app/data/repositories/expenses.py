"""Expense persistence (ADR-0043/0045): costs not embedded in a lot's basis —
write-down losses, selling fees, outbound hauling. Append-only: corrections are
reversing entries, never edits. The application owns the commit."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import LotExpense
from app.data.records import ExpenseRecord
from app.domain.lots import EntrySource, ExpenseKind


async def create_expense(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    kind: ExpenseKind,
    amount: Decimal,
    source: EntrySource,
    incurred_at: datetime,
    lot_id: uuid.UUID | None = None,
    external_ref: int | None = None,
    recorded_by_character_id: int | None = None,
    note: str | None = None,
) -> ExpenseRecord:
    expense = LotExpense(
        corporation_id=corporation_id,
        kind=kind,
        amount=amount,
        source=source,
        incurred_at=incurred_at,
        lot_id=lot_id,
        external_ref=external_ref,
        recorded_by_character_id=recorded_by_character_id,
        note=note,
    )
    session.add(expense)
    await session.flush()
    await session.refresh(expense)
    return ExpenseRecord.model_validate(expense)


async def list_for_corp(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> list[ExpenseRecord]:
    rows = (
        (
            await session.execute(
                select(LotExpense)
                .where(LotExpense.corporation_id == corporation_id)
                .order_by(LotExpense.incurred_at, LotExpense.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [ExpenseRecord.model_validate(row) for row in rows]
