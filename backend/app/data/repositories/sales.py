"""Sale persistence (ADR-0043/0045): one row per (sale event × lot consumed) — the
only place revenue is recorded. Append-only; `(channel, external_ref, lot_id)` is the
ESI idempotency key. The application owns the commit."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Sale
from app.data.records import SaleRecord
from app.domain.lots import EntrySource, SaleChannel


async def create_sale(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    lot_id: uuid.UUID,
    qty: int,
    unit_proceeds: Decimal,
    unit_cost: Decimal,
    cost_is_estimated: bool,
    channel: SaleChannel,
    source: EntrySource,
    sold_at: datetime,
    sales_tax: Decimal = Decimal(0),
    external_ref: int | None = None,
    recorded_by_character_id: int | None = None,
    note: str | None = None,
) -> SaleRecord:
    sale = Sale(
        corporation_id=corporation_id,
        lot_id=lot_id,
        qty=qty,
        unit_proceeds=unit_proceeds,
        unit_cost=unit_cost,
        cost_is_estimated=cost_is_estimated,
        sales_tax=sales_tax,
        channel=channel,
        source=source,
        external_ref=external_ref,
        sold_at=sold_at,
        recorded_by_character_id=recorded_by_character_id,
        note=note,
    )
    session.add(sale)
    await session.flush()
    await session.refresh(sale)
    return SaleRecord.model_validate(sale)


async def external_refs_for_channel(
    session: AsyncSession, *, corporation_id: uuid.UUID, channel: SaleChannel
) -> set[int]:
    """The already-recorded ESI ids for a channel — the ingestion's dedupe set."""
    rows = (
        await session.execute(
            select(Sale.external_ref).where(
                Sale.corporation_id == corporation_id,
                Sale.channel == channel,
                Sale.external_ref.is_not(None),
            )
        )
    ).scalars()
    return set(rows)


async def set_sales_tax_for_ref(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    channel: SaleChannel,
    external_ref: int,
    sales_tax: Decimal,
) -> bool:
    """Attach a journal tax entry to its fill: SET (not add) the tax on the FIRST
    sale row of the transaction, so re-polling the journal is idempotent and the
    transaction's total profit stays exact even when the fill split across lots.
    Returns False when the fill isn't recorded (yet)."""
    rows = (
        (
            await session.execute(
                select(Sale)
                .where(
                    Sale.corporation_id == corporation_id,
                    Sale.channel == channel,
                    Sale.external_ref == external_ref,
                )
                .order_by(Sale.created_at, Sale.id)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return False
    for i, row in enumerate(rows):
        row.sales_tax = sales_tax if i == 0 else Decimal(0)
    await session.flush()
    return True


async def list_between(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[SaleRecord]:
    """Sales sold in [since, until) — the profit view's period read (#159). Either
    bound may be None (open-ended)."""
    stmt = select(Sale).where(Sale.corporation_id == corporation_id)
    if since is not None:
        stmt = stmt.where(Sale.sold_at >= since)
    if until is not None:
        stmt = stmt.where(Sale.sold_at < until)
    rows = (await session.execute(stmt.order_by(Sale.sold_at))).scalars().all()
    return [SaleRecord.model_validate(row) for row in rows]


async def list_for_corp(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> list[SaleRecord]:
    rows = (
        (
            await session.execute(
                select(Sale)
                .where(Sale.corporation_id == corporation_id)
                .order_by(Sale.sold_at.desc(), Sale.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [SaleRecord.model_validate(row) for row in rows]
