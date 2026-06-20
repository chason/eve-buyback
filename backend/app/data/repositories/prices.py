"""Read/write access to the market-price cache (ADR-0006).

Reads return Pydantic records; the upsert sets `fetched_at` from values supplied by
the application layer (which owns the clock and the `commit()`).
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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

# PostgreSQL caps a statement at 65535 bind parameters. Each upserted row carries 17
# (hub_id + type_id + the 15 aggregate columns), so a full structure order book (~8k
# types) in one INSERT blows the limit — the statement errors *and* building a multi-MB
# statement stalls the event loop. Chunk well under the ceiling: 1000 × 17 = 17000.
_MAX_ROWS_PER_INSERT = 1000


async def get_prices(
    session: AsyncSession, *, hub_id: str, type_ids: Sequence[int]
) -> list[MarketPriceRecord]:
    if not type_ids:
        return []
    stmt = select(MarketPrice).where(
        MarketPrice.hub_id == hub_id, MarketPrice.type_id.in_(type_ids)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [MarketPriceRecord.model_validate(r) for r in rows]


async def list_type_ids_for_hub(
    session: AsyncSession, *, hub_id: str, older_than: datetime | None = None
) -> list[int]:
    """The type ids cached for a hub — the "hot set" someone has priced there. With
    `older_than`, only those whose `fetched_at` predates it (the refresh-due set for an
    ESI-region hub, ADR-0034)."""
    stmt = select(MarketPrice.type_id).where(MarketPrice.hub_id == hub_id)
    if older_than is not None:
        stmt = stmt.where(MarketPrice.fetched_at < older_than)
    return list((await session.execute(stmt)).scalars().all())


async def latest_fetched_at(
    session: AsyncSession, *, hub_id: str
) -> datetime | None:
    """The most recent `fetched_at` across a hub's cached prices, or None if it has
    none. Used as the structure-refresh "due" proxy (ADR-0034): a structure's whole
    book is fetched at once, so its freshest row dates the last full refresh."""
    stmt = select(func.max(MarketPrice.fetched_at)).where(
        MarketPrice.hub_id == hub_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def upsert_prices(
    session: AsyncSession, *, hub_id: str, rows: Sequence[dict]
) -> None:
    """Insert-or-update cached prices keyed by `(hub_id, type_id)`. Each row dict
    carries `type_id`, the buy/sell aggregate fields, and `fetched_at`."""
    if not rows:
        return
    values = [{**row, "hub_id": hub_id} for row in rows]
    # Chunk so no single statement exceeds Postgres's 65535-parameter limit (a large
    # structure book otherwise fails and stalls the loop). All chunks share the caller's
    # unit of work, so they still commit atomically.
    for start in range(0, len(values), _MAX_ROWS_PER_INSERT):
        chunk = values[start : start + _MAX_ROWS_PER_INSERT]
        stmt = pg_insert(MarketPrice).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=[MarketPrice.hub_id, MarketPrice.type_id],
            set_={col: getattr(stmt.excluded, col) for col in _AGGREGATE_COLUMNS},
        )
        await session.execute(stmt)
