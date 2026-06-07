"""Market-price use case (ADR-0006): a read-through cache over Fuzzwork.

Returns cached prices that are still fresh, fetches the misses/stale ones from
Fuzzwork, and writes them back. Owns the unit of work (commit). Degrades
gracefully on a Fuzzwork outage: serves whatever (possibly stale) cache exists and
simply omits items it has never priced, rather than failing the whole request.
"""

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.data.records import MarketPriceRecord
from app.data.repositories import prices as prices_repo
from app.domain.market import is_fresh
from app.plugins.fuzzwork import FuzzworkAggregate, FuzzworkClient

log = logging.getLogger(__name__)


def _as_utc(dt: datetime) -> datetime:
    """Cached timestamps come back naive from SQLite (no tz support); we always
    store UTC, so treat a naive value as UTC for freshness math."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _row_from_aggregate(type_id: int, agg: FuzzworkAggregate, fetched_at) -> dict:
    return {
        "type_id": type_id,
        "buy_weighted_average": agg.buy.weighted_average,
        "buy_max": agg.buy.max,
        "buy_min": agg.buy.min,
        "buy_median": agg.buy.median,
        "buy_percentile": agg.buy.percentile,
        "buy_volume": agg.buy.volume,
        "buy_order_count": agg.buy.order_count,
        "sell_weighted_average": agg.sell.weighted_average,
        "sell_max": agg.sell.max,
        "sell_min": agg.sell.min,
        "sell_median": agg.sell.median,
        "sell_percentile": agg.sell.percentile,
        "sell_volume": agg.sell.volume,
        "sell_order_count": agg.sell.order_count,
        "fetched_at": fetched_at,
    }


async def get_market_prices(
    session: AsyncSession,
    fuzzwork: FuzzworkClient,
    *,
    hub_id: int,
    type_ids: list[int],
    now,
) -> list[MarketPriceRecord]:
    if not type_ids:
        return []

    ttl = get_settings().market_cache_ttl_seconds
    cached = {
        r.type_id: r
        for r in await prices_repo.get_prices(
            session, hub_id=hub_id, type_ids=type_ids
        )
    }
    fresh = {
        tid: r
        for tid, r in cached.items()
        if is_fresh(_as_utc(r.fetched_at), now=now, ttl_seconds=ttl)
    }
    to_fetch = [tid for tid in type_ids if tid not in fresh]

    refreshed: dict[int, MarketPriceRecord] = {}
    if to_fetch:
        try:
            aggregates = await fuzzwork.get_aggregates(
                station=hub_id, type_ids=to_fetch
            )
        except httpx.HTTPError:
            log.warning(
                "Fuzzwork fetch failed for hub %s (%d types); serving cache",
                hub_id,
                len(to_fetch),
                exc_info=True,
            )
            aggregates = {}

        if aggregates:
            rows = [
                _row_from_aggregate(tid, agg, now)
                for tid, agg in aggregates.items()
            ]
            await prices_repo.upsert_prices(session, hub_id=hub_id, rows=rows)
            await session.commit()
            refreshed = {
                row["type_id"]: MarketPriceRecord(hub_id=hub_id, **row)
                for row in rows
            }

    # Prefer fresh-fetched, fall back to stale cache, omit the never-priced.
    result: list[MarketPriceRecord] = []
    for tid in type_ids:
        record = refreshed.get(tid) or fresh.get(tid) or cached.get(tid)
        if record is not None:
            result.append(record)
    return result
