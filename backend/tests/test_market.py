from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from app.application.market import get_market_prices
from app.data.db import SessionLocal
from app.data.records import MarketPriceRecord
from app.data.repositories import prices as prices_repo
from app.domain.aggregates import OrderBookAggregate, SideAggregate
from app.domain.market import HubDescriptor
from app.plugins.cache import MemoryCache, get_model, safe_key, set_model
from app.plugins.fuzzwork import FuzzworkAggregate, FuzzworkSide

HUB = "60003760"  # Jita — a Fuzzwork hub
FUZ_HUB = HubDescriptor(hub_id=HUB, kind="npc_station")
# A non-Fuzzwork NPC station (priced via ESI region orders), with its region cached.
ESI_HUB = HubDescriptor(hub_id="60012345", kind="npc_station", region_id=10000002)
STRUCT_HUB = HubDescriptor(hub_id="1035000000001", kind="structure")
NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC)


def _aggregate(buy: str, sell: str) -> FuzzworkAggregate:
    """Build an aggregate whose percentile fields carry recognizable markers.
    Values are passed as strings so they parse to exact Decimals."""
    return FuzzworkAggregate(
        buy=FuzzworkSide(
            weightedAverage=buy, max=buy, min=buy, median=buy,
            percentile=buy, volume="100", orderCount=5,
        ),
        sell=FuzzworkSide(
            weightedAverage=sell, max=sell, min=sell, median=sell,
            percentile=sell, volume="100", orderCount=5,
        ),
    )


def _esi_book(buy: str, sell: str) -> OrderBookAggregate:
    def side(value: str) -> SideAggregate:
        m = Decimal(value)
        return SideAggregate(
            weighted_average=m, max=m, min=m, median=m, percentile=m,
            volume=Decimal("100"), order_count=5,
        )

    return OrderBookAggregate(buy=side(buy), sell=side(sell))


def _price_row(type_id: int, marker: str, fetched_at: datetime) -> dict:
    m = Decimal(marker)
    return {
        "type_id": type_id,
        "buy_weighted_average": m, "buy_max": m, "buy_min": m,
        "buy_median": m, "buy_percentile": m, "buy_volume": Decimal("1"),
        "buy_order_count": 1,
        "sell_weighted_average": m, "sell_max": m, "sell_min": m,
        "sell_median": m, "sell_percentile": m, "sell_volume": Decimal("1"),
        "sell_order_count": 1,
        "fetched_at": fetched_at,
    }


class FakeFuzzwork:
    def __init__(self, response=None, error=None):
        self.response = response or {}
        self.error = error
        self.calls = 0
        self.type_ids_seen: list[list[int]] = []  # the ids of each call, for assertions

    async def get_aggregates(self, *, station: int, type_ids: list[int]):
        self.calls += 1
        self.type_ids_seen.append(list(type_ids))
        if self.error is not None:
            raise self.error
        return {tid: self.response[tid] for tid in type_ids if tid in self.response}


class FakeEsiMarket:
    def __init__(self, response=None, error=None):
        self.response = response or {}
        self.error = error
        self.region_calls = 0
        self.structure_calls = 0

    async def get_region_aggregates(self, *, region_id, station_id, type_ids):
        self.region_calls += 1
        if self.error is not None:
            raise self.error
        return {tid: self.response[tid] for tid in type_ids if tid in self.response}

    async def get_structure_aggregates(self, *, structure_id, type_ids, access_token):
        self.structure_calls += 1
        if self.error is not None:
            raise self.error
        return {tid: self.response[tid] for tid in type_ids if tid in self.response}


async def _seed_cache(rows: list[dict], hub_id: str = HUB) -> None:
    async with SessionLocal() as session:
        await prices_repo.upsert_prices(session, hub_id=hub_id, rows=rows)
        await session.commit()


async def test_cache_miss_fetches_and_stores():
    fuzz = FakeFuzzwork(response={34: _aggregate(buy="5.0", sell="8.0")})
    esi = FakeEsiMarket()
    async with SessionLocal() as session:
        result = await get_market_prices(
            session, fuzz, esi, hub=FUZ_HUB, type_ids=[34], now=NOW
        )
    assert fuzz.calls == 1
    assert esi.region_calls == 0  # a Fuzzwork hub never touches ESI
    assert len(result) == 1
    assert result[0].type_id == 34
    assert result[0].buy_percentile == Decimal("5.0")

    # It was written through to the cache.
    async with SessionLocal() as session:
        cached = await prices_repo.get_prices(session, hub_id=HUB, type_ids=[34])
    assert cached[0].sell_percentile == Decimal("8.0")


async def test_fresh_cache_does_not_call_source():
    await _seed_cache([_price_row(34, marker="1.0", fetched_at=NOW)])
    fuzz = FakeFuzzwork(response={34: _aggregate(buy="99.0", sell="99.0")})
    esi = FakeEsiMarket()
    async with SessionLocal() as session:
        result = await get_market_prices(
            session, fuzz, esi, hub=FUZ_HUB, type_ids=[34], now=NOW
        )
    assert fuzz.calls == 0
    assert result[0].buy_percentile == Decimal("1.0")  # served from cache


async def test_stale_cache_is_refetched():
    stale_at = NOW - timedelta(seconds=7200)  # older than the 1h TTL
    await _seed_cache([_price_row(34, marker="1.0", fetched_at=stale_at)])
    fuzz = FakeFuzzwork(response={34: _aggregate(buy="42.0", sell="43.0")})
    esi = FakeEsiMarket()
    async with SessionLocal() as session:
        result = await get_market_prices(
            session, fuzz, esi, hub=FUZ_HUB, type_ids=[34], now=NOW
        )
    assert fuzz.calls == 1
    assert result[0].buy_percentile == Decimal("42.0")  # refreshed value


async def test_outage_serves_stale_and_omits_unpriced():
    stale_at = NOW - timedelta(seconds=7200)
    await _seed_cache([_price_row(34, marker="1.0", fetched_at=stale_at)])
    fuzz = FakeFuzzwork(error=httpx.HTTPError("fuzzwork down"))
    esi = FakeEsiMarket()
    async with SessionLocal() as session:
        result = await get_market_prices(
            session, fuzz, esi, hub=FUZ_HUB, type_ids=[34, 999], now=NOW
        )
    assert fuzz.calls == 1
    # 34 falls back to stale cache; 999 (never priced) is omitted, no exception.
    assert [r.type_id for r in result] == [34]
    assert result[0].buy_percentile == Decimal("1.0")


async def test_non_fuzzwork_hub_prices_from_esi_region_orders():
    fuzz = FakeFuzzwork(response={34: _aggregate(buy="99.0", sell="99.0")})
    esi = FakeEsiMarket(response={34: _esi_book(buy="5.0", sell="8.0")})
    async with SessionLocal() as session:
        result = await get_market_prices(
            session, fuzz, esi, hub=ESI_HUB, type_ids=[34], now=NOW
        )
    assert esi.region_calls == 1
    assert fuzz.calls == 0  # ESI hub never touches Fuzzwork
    assert result[0].buy_percentile == Decimal("5.0")
    assert result[0].sell_percentile == Decimal("8.0")

    # Written through under the ESI hub's id.
    async with SessionLocal() as session:
        cached = await prices_repo.get_prices(
            session, hub_id=ESI_HUB.hub_id, type_ids=[34]
        )
    assert cached and cached[0].buy_weighted_average == Decimal("5.0")


async def test_structure_hub_prices_via_provider_token():
    esi = FakeEsiMarket(response={34: _esi_book(buy="3.0", sell="4.0")})
    calls = {"token": 0}

    async def provider() -> str:
        calls["token"] += 1
        return "fresh-access-token"

    async with SessionLocal() as session:
        result = await get_market_prices(
            session, FakeFuzzwork(), esi, hub=STRUCT_HUB, type_ids=[34], now=NOW,
            corp_esi_token_provider=provider,
        )
    assert calls["token"] == 1
    assert esi.structure_calls == 1
    assert result[0].buy_percentile == Decimal("3.0")


async def test_structure_missing_token_degrades_to_unpriced():
    from app.application.errors import CorpEsiTokenMissing

    esi = FakeEsiMarket(response={34: _esi_book(buy="3.0", sell="4.0")})

    async def provider() -> str:
        raise CorpEsiTokenMissing()

    async with SessionLocal() as session:
        result = await get_market_prices(
            session, FakeFuzzwork(), esi, hub=STRUCT_HUB, type_ids=[34], now=NOW,
            corp_esi_token_provider=provider,
        )
    # No token + no cache → the type is simply omitted; the appraisal never errors.
    assert result == []


async def test_esi_outage_serves_stale_cache():
    stale_at = NOW - timedelta(seconds=7200)
    await _seed_cache(
        [_price_row(34, marker="1.0", fetched_at=stale_at)], hub_id=ESI_HUB.hub_id
    )
    fuzz = FakeFuzzwork()
    esi = FakeEsiMarket(error=httpx.HTTPError("esi down"))
    async with SessionLocal() as session:
        result = await get_market_prices(
            session, fuzz, esi, hub=ESI_HUB, type_ids=[34, 999], now=NOW
        )
    assert esi.region_calls == 1
    assert [r.type_id for r in result] == [34]
    assert result[0].buy_percentile == Decimal("1.0")


# --- L1 cache tier (ADR-0033) ---


def _spy_get_prices(monkeypatch) -> dict:
    """Record DB-cache reads (count + the type_ids actually queried) so an L1 hit can
    be proven to skip the DB and the L2 query proven to ask only for the L1 misses."""
    spy = {"n": 0, "type_ids": []}
    original = prices_repo.get_prices

    async def counting(session, *, hub_id, type_ids):
        spy["n"] += 1
        spy["type_ids"].append(list(type_ids))
        return await original(session, hub_id=hub_id, type_ids=type_ids)

    monkeypatch.setattr(
        "app.application.market.prices_repo.get_prices", counting
    )
    return spy


class _RaisingCache:
    """A cache backend whose every op raises — stands in for an unreachable memcached
    that *isn't* best-effort, to prove get_market_prices doesn't propagate cache errors."""

    async def get(self, key):
        raise ConnectionRefusedError("cache down")

    async def set(self, key, value, *, ttl_seconds):
        raise ConnectionRefusedError("cache down")

    async def delete(self, key):
        raise ConnectionRefusedError("cache down")

    async def aclose(self):
        pass


async def test_cache_backend_outage_does_not_break_pricing():
    # Even if the L1 cache raises on every call, pricing still serves from the source
    # (and DB), never propagating the cache error to the caller.
    fuzz = FakeFuzzwork(response={34: _aggregate(buy="5.0", sell="8.0")})
    async with SessionLocal() as session:
        result = await get_market_prices(
            session, fuzz, FakeEsiMarket(), hub=FUZ_HUB, type_ids=[34], now=NOW,
            cache=_RaisingCache(),
        )
    assert fuzz.calls == 1
    assert result[0].buy_percentile == Decimal("5.0")  # priced despite the cache outage


async def test_l1_mixed_basket_queries_only_the_misses(monkeypatch):
    # 34 in L1, 35 fresh in DB, 36 only at the source. Assert each tier is consulted
    # for exactly the right ids and the merged result is in requested order.
    cache = MemoryCache()
    await set_model(
        cache, safe_key("mp", HUB, 34),
        MarketPriceRecord(hub_id=HUB, **_price_row(34, "7.0", NOW)), ttl_seconds=60,
    )
    await _seed_cache([_price_row(35, marker="2.0", fetched_at=NOW)])  # fresh DB row

    spy = _spy_get_prices(monkeypatch)
    fuzz = FakeFuzzwork(response={36: _aggregate(buy="9.0", sell="9.0")})
    async with SessionLocal() as session:
        result = await get_market_prices(
            session, fuzz, FakeEsiMarket(), hub=FUZ_HUB, type_ids=[34, 35, 36],
            now=NOW, cache=cache,
        )
    # DB queried for the L1 misses only ([35, 36]); source for the both-misses only ([36]).
    assert spy["type_ids"] == [[35, 36]]
    assert fuzz.type_ids_seen == [[36]]
    # Result in requested order, each from its tier.
    by_id = {r.type_id: r.buy_percentile for r in result}
    assert [r.type_id for r in result] == [34, 35, 36]
    assert by_id == {34: Decimal("7.0"), 35: Decimal("2.0"), 36: Decimal("9.0")}


async def test_l1_hit_skips_db_and_source(monkeypatch):
    cache = MemoryCache()
    # Prime L1 directly with a record for type 34.
    seeded = MarketPriceRecord(hub_id=HUB, **_price_row(34, "7.0", NOW))
    await set_model(cache, safe_key("mp", HUB, 34), seeded, ttl_seconds=60)

    db_reads = _spy_get_prices(monkeypatch)
    fuzz = FakeFuzzwork(response={34: _aggregate(buy="99.0", sell="99.0")})
    async with SessionLocal() as session:
        result = await get_market_prices(
            session, fuzz, FakeEsiMarket(), hub=FUZ_HUB, type_ids=[34], now=NOW,
            cache=cache,
        )
    assert db_reads["n"] == 0          # never touched the DB
    assert fuzz.calls == 0             # never touched the source
    assert result[0].buy_percentile == Decimal("7.0")
    # sanity: the helper used above resolves the same key the use case writes
    assert await get_model(cache, safe_key("mp", HUB, 34), MarketPriceRecord)


async def test_l1_populated_from_fresh_db():
    await _seed_cache([_price_row(34, marker="2.0", fetched_at=NOW)])  # fresh DB row
    cache = MemoryCache()
    fuzz = FakeFuzzwork()
    async with SessionLocal() as session:
        result = await get_market_prices(
            session, fuzz, FakeEsiMarket(), hub=FUZ_HUB, type_ids=[34], now=NOW,
            cache=cache,
        )
    assert fuzz.calls == 0  # DB was fresh
    assert result[0].buy_percentile == Decimal("2.0")
    promoted = await get_model(cache, safe_key("mp", HUB, 34), MarketPriceRecord)
    assert promoted is not None and promoted.buy_percentile == Decimal("2.0")


async def test_l1_populated_from_source_fetch():
    cache = MemoryCache()  # empty L1, empty DB
    fuzz = FakeFuzzwork(response={34: _aggregate(buy="5.0", sell="8.0")})
    async with SessionLocal() as session:
        await get_market_prices(
            session, fuzz, FakeEsiMarket(), hub=FUZ_HUB, type_ids=[34], now=NOW,
            cache=cache,
        )
    assert fuzz.calls == 1
    cached = await get_model(cache, safe_key("mp", HUB, 34), MarketPriceRecord)
    assert cached is not None and cached.buy_percentile == Decimal("5.0")


async def test_l1_not_poisoned_by_stale_fallback():
    stale_at = NOW - timedelta(seconds=7200)
    await _seed_cache([_price_row(34, marker="1.0", fetched_at=stale_at)])
    cache = MemoryCache()
    fuzz = FakeFuzzwork(error=httpx.HTTPError("fuzzwork down"))
    async with SessionLocal() as session:
        result = await get_market_prices(
            session, fuzz, FakeEsiMarket(), hub=FUZ_HUB, type_ids=[34], now=NOW,
            cache=cache,
        )
    # Serves stale from the DB, but never writes stale data into L1 (so the next
    # request retries the source rather than locking in a stale value).
    assert result[0].buy_percentile == Decimal("1.0")
    assert await get_model(cache, safe_key("mp", HUB, 34), MarketPriceRecord) is None
