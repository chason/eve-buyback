"""Market-price use case (ADR-0006, ADR-0028): a read-through cache over whichever
source covers the hub.

Returns cached prices that are still fresh, fetches the misses/stale ones from the
hub's source (Fuzzwork for the 5 covered hubs; ESI region orders for any other NPC
station), and writes them back. Owns the unit of work (commit). Degrades gracefully
on a source outage: serves whatever (possibly stale) cache exists and simply omits
items it has never priced, rather than failing the whole request.
"""

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.data.records import MarketPriceRecord
from app.data.repositories import prices as prices_repo
from app.domain.market import HubDescriptor, is_fresh, resolve_market_source
from app.plugins.esi_market import EsiMarketClient
from app.plugins.fuzzwork import FuzzworkClient

log = logging.getLogger(__name__)


def _row_from_aggregate(type_id: int, agg, fetched_at) -> dict:
    """Build a `market_prices` row from any buy/sell aggregate — Fuzzwork's
    `FuzzworkAggregate` or ESI's `OrderBookAggregate` share the 7-field side shape."""
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


async def _fetch_aggregates(
    fuzzwork: FuzzworkClient,
    esi_market: EsiMarketClient,
    hub: HubDescriptor,
    type_ids: list[int],
) -> dict:
    """Fetch buy/sell aggregates for the cache misses from the hub's source. Returns
    `{}` on any outage so the caller falls back to cached prices."""
    source = resolve_market_source(hub)
    try:
        if source == "fuzzwork":
            return await fuzzwork.get_aggregates(station=hub.hub_id, type_ids=type_ids)
        if source == "esi_region":
            if hub.region_id is None:
                log.warning("hub %s has no region_id; cannot price via ESI", hub.hub_id)
                return {}
            return await esi_market.get_region_aggregates(
                region_id=hub.region_id, station_id=hub.hub_id, type_ids=type_ids
            )
        # esi_structure → a later phase; treat as unpriced for now.
        log.warning("structure pricing not yet available (hub %s)", hub.hub_id)
        return {}
    except httpx.HTTPError:
        log.warning(
            "market fetch failed (source=%s) for hub %s; serving cache",
            source,
            hub.hub_id,
            exc_info=True,
        )
        return {}


async def get_market_prices(
    session: AsyncSession,
    fuzzwork: FuzzworkClient,
    esi_market: EsiMarketClient,
    *,
    hub: HubDescriptor,
    type_ids: list[int],
    now,
) -> list[MarketPriceRecord]:
    if not type_ids:
        return []

    ttl = get_settings().market_cache_ttl_seconds
    cached = {
        r.type_id: r
        for r in await prices_repo.get_prices(
            session, hub_id=hub.hub_id, type_ids=type_ids
        )
    }
    fresh = {
        tid: r
        for tid, r in cached.items()
        if is_fresh(r.fetched_at, now=now, ttl_seconds=ttl)
    }
    to_fetch = [tid for tid in type_ids if tid not in fresh]

    refreshed: dict[int, MarketPriceRecord] = {}
    if to_fetch:
        aggregates = await _fetch_aggregates(fuzzwork, esi_market, hub, to_fetch)
        if aggregates:
            rows = [
                _row_from_aggregate(tid, agg, now)
                for tid, agg in aggregates.items()
            ]
            await prices_repo.upsert_prices(session, hub_id=hub.hub_id, rows=rows)
            await session.commit()
            refreshed = {
                row["type_id"]: MarketPriceRecord(hub_id=hub.hub_id, **row)
                for row in rows
            }

    # Prefer fresh-fetched, fall back to stale cache, omit the never-priced.
    result: list[MarketPriceRecord] = []
    for tid in type_ids:
        record = refreshed.get(tid) or fresh.get(tid) or cached.get(tid)
        if record is not None:
            result.append(record)
    return result
