"""Corp market-order snapshot persistence (ADR-0045, #156): replaced wholesale each
sales-ingestion pass; read as the "listed" allocation the reconciliation subtracts
from expected hangar stock. The application owns the commit."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import TypedDict

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import CorpMarketOrder


class OrderRow(TypedDict):
    """One live order as this repository stores it (#188) — the input contract for
    `replace_for_corp`, owned here so callers build a checkable shape instead of
    folklore dicts. `location_id` is a string (ADR-0029 id convention)."""

    order_id: int
    type_id: int
    is_buy_order: bool
    price: Decimal
    volume_remain: int
    volume_total: int
    location_id: str
    wallet_division: int
    issued: datetime


async def replace_for_corp(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    orders: Sequence[OrderRow],
) -> None:
    """Make the snapshot match the live orders: delete-and-insert (the set is small
    and vanished orders must vanish)."""
    await session.execute(
        delete(CorpMarketOrder).where(
            CorpMarketOrder.corporation_id == corporation_id
        )
    )
    for order in orders:
        session.add(CorpMarketOrder(corporation_id=corporation_id, **order))
    await session.flush()


async def listed_by_location_type(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> dict[tuple[str, int], int]:
    """Units sitting in SELL-order escrow per `(location_id, type_id)` — physically
    out of the hangar, so the reconciliation subtracts them from expected stock
    (`qty_idle`, ADR-0043/0044)."""
    rows = (
        await session.execute(
            select(
                CorpMarketOrder.location_id,
                CorpMarketOrder.type_id,
                func.sum(CorpMarketOrder.volume_remain),
            )
            .where(
                CorpMarketOrder.corporation_id == corporation_id,
                CorpMarketOrder.is_buy_order.is_(False),
            )
            .group_by(CorpMarketOrder.location_id, CorpMarketOrder.type_id)
        )
    ).all()
    return {(loc, tid): int(qty) for loc, tid, qty in rows}


async def sell_orders_off_division(
    session: AsyncSession, *, corporation_id: uuid.UUID, division: int
) -> list[tuple[str, int, int, Decimal]]:
    """Sell orders paying into a division OTHER than the configured buyback one
    (ADR-0045's guard): `(location_id, type_id, volume_remain, price)` rows for the
    reconciliation log."""
    rows = (
        await session.execute(
            select(
                CorpMarketOrder.location_id,
                CorpMarketOrder.type_id,
                CorpMarketOrder.volume_remain,
                CorpMarketOrder.price,
            ).where(
                CorpMarketOrder.corporation_id == corporation_id,
                CorpMarketOrder.is_buy_order.is_(False),
                CorpMarketOrder.wallet_division != division,
            )
        )
    ).all()
    return [(loc, tid, int(qty), price) for loc, tid, qty, price in rows]


async def last_synced_at(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> datetime | None:
    return (
        await session.execute(
            select(func.max(CorpMarketOrder.synced_at)).where(
                CorpMarketOrder.corporation_id == corporation_id
            )
        )
    ).scalar_one_or_none()
