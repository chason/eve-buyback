"""The manual-entry escape hatch (ADR-0045, #158): what ESI can't see, a manager
records by hand — an off-game sale, known-cost off-app stock, an expense, or ISK
owed to the buyback (the wrong-wallet receivable). Everything lands in the SAME
sales/lots/expenses tables tagged `source='manual'` + who entered it + a required
note, so there is one set of books, with provenance — never a side-ledger.

Two independent flags, never conflated: `source` (esi | manual | system) is
PROVENANCE; `cost_is_estimated` is COST CONFIDENCE — a manual lot with a known
price is manual AND measured.

Corrections are reversing entries, never edits or deletes: a mistaken expense is
offset by a negative-amount expense whose note says what it reverses; nothing here
(or anywhere in the ledger) exposes an update or delete of a booked row.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.application import entitlements as entitlements_app
from app.application import sales as sales_app
from app.application.corporations import get_registered_corporation
from app.application.errors import ReceivableNotFound
from app.data.records import LotRecord, ReceivableRecord
from app.data.repositories import expenses as expenses_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import receivables as receivables_repo
from app.domain.lots import ExpenseKind


async def _gated_corp(session: AsyncSession, corporation_eve_id: int):
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    return corp


async def record_manual_sale(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    type_id: int,
    qty: int,
    unit_proceeds: Decimal,
    location_id: str,
    note: str,
    entered_by_character_id: int,
    sold_at: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    """An off-game (direct) sale: FIFO-consumes lots through the same shared path
    as the ESI channels — including the deemed-COGS no-lot fallback — tagged
    `channel='direct'`, `source='manual'`. Returns True when the fallback fired
    (the UI tells the manager their books didn't have that stock). Owns the
    commit."""
    corp = await _gated_corp(session, corporation_eve_id)
    now = now or datetime.now(UTC)
    unmatched = await sales_app.consume_and_record_sale(
        session,
        corp.id,
        type_id=type_id,
        qty=qty,
        unit_proceeds=unit_proceeds,
        location_id=location_id,
        channel="direct",
        sold_at=sold_at or now,
        now=now,
        source="manual",
        recorded_by_character_id=entered_by_character_id,
        note=note,
    )
    await session.commit()
    return unmatched


async def record_manual_lot(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    type_id: int,
    qty: int,
    unit_cost: Decimal,
    location_id: str,
    note: str,
    entered_by_character_id: int,
    cost_is_estimated: bool = False,
    acquired_at: datetime | None = None,
    now: datetime | None = None,
) -> LotRecord:
    """Known-cost off-app stock (bought outside the app, cost known from the deal).
    `cost_is_estimated` stays the manager's call: False when they know what was
    paid (manual AND measured), True when the price is their best guess. Owns the
    commit."""
    corp = await _gated_corp(session, corporation_eve_id)
    now = now or datetime.now(UTC)
    lot = await lots_repo.create_lot(
        session,
        corporation_id=corp.id,
        item_type_id=type_id,
        qty=qty,
        unit_purchase_cost=unit_cost,
        acquired_at=acquired_at or now,
        source="manual",
        cost_is_estimated=cost_is_estimated,
        location_id=location_id,
        notes=note,
    )
    await session.commit()
    return lot


async def record_manual_expense(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    kind: ExpenseKind,
    amount: Decimal,
    note: str,
    entered_by_character_id: int,
    incurred_at: datetime | None = None,
    now: datetime | None = None,
) -> None:
    """A cost ESI didn't book — outbound hauling, a fee, anything. A NEGATIVE
    amount is the correction path (ADR-0045): a reversing entry whose note says
    what it offsets; booked rows are never edited or deleted. Owns the commit."""
    corp = await _gated_corp(session, corporation_eve_id)
    now = now or datetime.now(UTC)
    await expenses_repo.create_expense(
        session,
        corporation_id=corp.id,
        kind=kind,
        amount=amount,
        source="manual",
        incurred_at=incurred_at or now,
        recorded_by_character_id=entered_by_character_id,
        note=note,
    )
    await session.commit()


async def create_receivable(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    amount: Decimal,
    note: str,
    entered_by_character_id: int,
    now: datetime | None = None,
) -> ReceivableRecord:
    """ISK owed to the buyback (ADR-0045): a sale paid into the wrong wallet or a
    personal wallet. A cash-location fact, NOT revenue — the sale event carries the
    revenue; this only keeps the amount an honest asset until the ISK moves. Owns
    the commit."""
    corp = await _gated_corp(session, corporation_eve_id)
    record = await receivables_repo.create(
        session,
        corporation_id=corp.id,
        amount=amount,
        note=note,
        incurred_at=now or datetime.now(UTC),
        entered_by_character_id=entered_by_character_id,
    )
    await session.commit()
    return record


async def list_receivables(
    session: AsyncSession, *, corporation_eve_id: int
) -> list[ReceivableRecord]:
    corp = await _gated_corp(session, corporation_eve_id)
    return await receivables_repo.list_for_corp(session, corporation_id=corp.id)


async def clear_receivable(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    receivable_id: uuid.UUID,
    cleared_by_character_id: int,
    cleared_note: str | None = None,
    now: datetime | None = None,
) -> ReceivableRecord:
    """The ISK arrived (the ESI-visible transfer happened, or it was settled some
    other way) — close the receivable. No revenue is booked here, so there is no
    double count. Idempotent on an already-cleared row. Owns the commit."""
    corp = await _gated_corp(session, corporation_eve_id)
    record = await receivables_repo.clear(
        session,
        corporation_id=corp.id,
        receivable_id=receivable_id,
        cleared_at=now or datetime.now(UTC),
        cleared_by_character_id=cleared_by_character_id,
        cleared_note=cleared_note,
    )
    if record is None:
        raise ReceivableNotFound()
    await session.commit()
    return record
