"""Market-price use case (ADR-0006, ADR-0028): a read-through cache over whichever
source covers the hub.

Returns cached prices that are still fresh, fetches the misses/stale ones from the
hub's source (Fuzzwork for the 5 covered hubs; ESI region orders for any other NPC
station), and writes them back. Owns the unit of work (commit). Degrades gracefully
on a source outage: serves whatever (possibly stale) cache exists and simply omits
items it has never priced, rather than failing the whole request.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import (
    StructureEncryptionNotConfigured,
    StructureTokenExpired,
    StructureTokenMissing,
)
from app.config import get_settings
from app.data.records import MarketPriceRecord
from app.data.repositories import prices as prices_repo
from app.domain.aggregates import BuySellAggregate
from app.domain.market import HubDescriptor, is_fresh, resolve_market_source
from app.plugins.cache import Cache, get_model, safe_key, set_model
from app.plugins.esi_market import EsiMarketClient, StructureAccessDenied
from app.plugins.fuzzwork import FuzzworkClient

log = logging.getLogger(__name__)

# Provides a fresh structure-market access token (refreshing server-side, ADR-0029).
StructureTokenProvider = Callable[[], Awaitable[str]]


def _l1_key(hub_id: str, type_id: int) -> str:
    # Keyed on hub_id only (matching the hub_id-keyed DB cache): the cached aggregate
    # is independent of region_id/kind — those only steer which source fills a miss
    # (resolve_market_source), not the cached content. Revisit this (and the DB key) if
    # cached content ever becomes region/kind-dependent.
    return safe_key("mp", hub_id, type_id)


def _row_from_aggregate(
    type_id: int, agg: BuySellAggregate, fetched_at: datetime
) -> dict:
    """Build a `market_prices` row from any buy/sell aggregate — Fuzzwork's
    `FuzzworkAggregate` or ESI's `OrderBookAggregate`, both typed as `BuySellAggregate`
    so the shared 7-field side shape is a contract, not a coincidence (#19)."""
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
    structure_token_provider: StructureTokenProvider | None,
) -> dict[int, BuySellAggregate]:
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
        # esi_structure (ADR-0029): needs a per-corp access token.
        if structure_token_provider is None:
            log.warning("no structure token provider for hub %s", hub.hub_id)
            return {}
        access_token = await structure_token_provider()
        return await esi_market.get_structure_aggregates(
            structure_id=hub.hub_id, type_ids=type_ids, access_token=access_token
        )
    except (
        StructureTokenMissing,
        StructureTokenExpired,
        StructureAccessDenied,
        StructureEncryptionNotConfigured,
    ):
        log.warning(
            "structure access unavailable for hub %s; serving cache", hub.hub_id
        )
        return {}
    except httpx.HTTPError:
        log.warning(
            "market fetch failed (source=%s) for hub %s; serving cache",
            source,
            hub.hub_id,
            exc_info=True,
        )
        return {}


async def persist_market_rows(
    session: AsyncSession,
    cache: Cache | None,
    *,
    hub_id: str,
    aggregates: dict[int, BuySellAggregate],
    now: datetime,
    l1_ttl: int,
) -> dict[int, MarketPriceRecord]:
    """Write freshly-fetched aggregates to the durable `market_prices` DB cache and,
    when an L1 cache is wired, promote them into it (ADR-0033). Returns the persisted
    records keyed by type id; a no-op on empty input. Shared by the lazy read-through
    (`get_market_prices`) and the background refresh (`market_refresh`), so both write
    the cache identically.

    This `commit` is a **deliberate, independent unit of work** (#21) — the documented
    exception to the one-use-case-one-UoW rule in `application/CLAUDE.md`. The market
    cache is shared infrastructure, not part of any caller's transaction:
    `create_appraisal` invokes the read-through mid-flight, *before* committing its
    appraisal row, so this cache fill lands in its own transaction by design. It is safe
    because `upsert_prices` is idempotent and a hub's price is identical for every corp:
    a crash after this commit but before the caller's leaves only a warm cache (benign),
    never a half-written appraisal. Committing here also lets the background refresh
    persist per hub, so one hub's failure can't roll back the others."""
    if not aggregates:
        return {}
    rows = [_row_from_aggregate(tid, agg, now) for tid, agg in aggregates.items()]
    await prices_repo.upsert_prices(session, hub_id=hub_id, rows=rows)
    await session.commit()
    records = {
        row["type_id"]: MarketPriceRecord(hub_id=hub_id, **row) for row in rows
    }
    if cache is not None:
        for tid, record in records.items():
            await set_model(cache, _l1_key(hub_id, tid), record, ttl_seconds=l1_ttl)
    return records


async def get_market_prices(
    session: AsyncSession,
    fuzzwork: FuzzworkClient,
    esi_market: EsiMarketClient,
    *,
    hub: HubDescriptor,
    type_ids: list[int],
    now: datetime,
    structure_token_provider: StructureTokenProvider | None = None,
    cache: Cache | None = None,
) -> list[MarketPriceRecord]:
    """Read prices through three tiers (ADR-0033): the pluggable L1 cache (in-memory
    or memcached), then the durable `market_prices` DB cache, then the hub's source.
    `cache=None` skips L1 (identical to the prior two-tier behavior). Only fresh data
    is promoted into L1, so a source outage still degrades to stale DB rows without
    poisoning the cache.

    Cache fills are committed in their own unit of work (see `persist_market_rows`),
    independent of any transaction the caller owns (#21)."""
    if not type_ids:
        return []

    settings = get_settings()
    ttl = settings.market_cache_ttl_seconds
    l1_ttl = settings.market_l1_cache_ttl_seconds

    # L1: hits are fresh by construction (the cache TTL enforces it).
    l1_hits: dict[int, MarketPriceRecord] = {}
    if cache is not None:
        for tid in type_ids:
            record = await get_model(cache, _l1_key(hub.hub_id, tid), MarketPriceRecord)
            if record is not None:
                l1_hits[tid] = record

    # L2 (DB) for the L1 misses; promote the fresh ones into L1.
    l2_ids = [tid for tid in type_ids if tid not in l1_hits]
    cached = (
        {
            r.type_id: r
            for r in await prices_repo.get_prices(
                session, hub_id=hub.hub_id, type_ids=l2_ids
            )
        }
        if l2_ids
        else {}
    )
    fresh = {
        tid: r
        for tid, r in cached.items()
        if is_fresh(r.fetched_at, now=now, ttl_seconds=ttl)
    }
    if cache is not None:
        for tid, record in fresh.items():
            await set_model(cache, _l1_key(hub.hub_id, tid), record, ttl_seconds=l1_ttl)

    to_fetch = [tid for tid in l2_ids if tid not in fresh]

    refreshed: dict[int, MarketPriceRecord] = {}
    if to_fetch:
        aggregates = await _fetch_aggregates(
            fuzzwork, esi_market, hub, to_fetch, structure_token_provider
        )
        refreshed = await persist_market_rows(
            session, cache, hub_id=hub.hub_id, aggregates=aggregates, now=now,
            l1_ttl=l1_ttl,
        )

    # Prefer L1, then fresh-fetched, then fresh DB, then stale cache; omit never-priced.
    result: list[MarketPriceRecord] = []
    for tid in type_ids:
        record = (
            l1_hits.get(tid)
            or refreshed.get(tid)
            or fresh.get(tid)
            or cached.get(tid)
        )
        if record is not None:
            result.append(record)
    return result
