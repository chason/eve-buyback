"""Read/write access to the market-price cache (ADR-0006).

Reads return Pydantic records; the upsert sets `fetched_at` from values supplied by
the application layer (which owns the clock and the `commit()`).
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import MarketPrice
from app.data.records import MarketPriceRecord

_AGGREGATE_COLUMNS = (
    "buy_weighted_average",
    "buy_max",
    "buy_min",
    "buy_median",
    "buy_percentile",
    "buy_volume",
    "buy_order_count",
    "sell_weighted_average",
    "sell_max",
    "sell_min",
    "sell_median",
    "sell_percentile",
    "sell_volume",
    "sell_order_count",
    "fetched_at",
)


async def get_prices(
    session: AsyncSession, *, hub_id: int, type_ids: Sequence[int]
) -> list[MarketPriceRecord]:
    if not type_ids:
        return []
    stmt = select(MarketPrice).where(
        MarketPrice.hub_id == hub_id, MarketPrice.type_id.in_(type_ids)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [MarketPriceRecord.model_validate(r) for r in rows]


async def upsert_prices(
    session: AsyncSession, *, hub_id: int, rows: Sequence[dict]
) -> None:
    """Insert-or-update cached prices keyed by `(hub_id, type_id)`. Each row dict
    carries `type_id`, the buy/sell aggregate fields, and `fetched_at`."""
    if not rows:
        return
    values = [{**row, "hub_id": hub_id} for row in rows]
    stmt = sqlite_insert(MarketPrice).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[MarketPrice.hub_id, MarketPrice.type_id],
        set_={col: getattr(stmt.excluded, col) for col in _AGGREGATE_COLUMNS},
    )
    await session.execute(stmt)
