from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from app.application.market import get_market_prices
from app.data.db import SessionLocal
from app.data.repositories import prices as prices_repo
from app.plugins.fuzzwork import FuzzworkAggregate, FuzzworkSide

HUB = 60003760
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

    async def get_aggregates(self, *, station: int, type_ids: list[int]):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {tid: self.response[tid] for tid in type_ids if tid in self.response}


async def _seed_cache(rows: list[dict]) -> None:
    async with SessionLocal() as session:
        await prices_repo.upsert_prices(session, hub_id=HUB, rows=rows)
        await session.commit()


async def test_cache_miss_fetches_and_stores():
    fuzz = FakeFuzzwork(response={34: _aggregate(buy="5.0", sell="8.0")})
    async with SessionLocal() as session:
        result = await get_market_prices(
            session, fuzz, hub_id=HUB, type_ids=[34], now=NOW
        )
    assert fuzz.calls == 1
    assert len(result) == 1
    assert result[0].type_id == 34
    assert result[0].buy_percentile == Decimal("5.0")

    # It was written through to the cache.
    async with SessionLocal() as session:
        cached = await prices_repo.get_prices(session, hub_id=HUB, type_ids=[34])
    assert cached[0].sell_percentile == Decimal("8.0")


async def test_fresh_cache_does_not_call_fuzzwork():
    await _seed_cache([_price_row(34, marker="1.0", fetched_at=NOW)])
    fuzz = FakeFuzzwork(response={34: _aggregate(buy="99.0", sell="99.0")})
    async with SessionLocal() as session:
        result = await get_market_prices(
            session, fuzz, hub_id=HUB, type_ids=[34], now=NOW
        )
    assert fuzz.calls == 0
    assert result[0].buy_percentile == Decimal("1.0")  # served from cache


async def test_stale_cache_is_refetched():
    stale_at = NOW - timedelta(seconds=7200)  # older than the 1h TTL
    await _seed_cache([_price_row(34, marker="1.0", fetched_at=stale_at)])
    fuzz = FakeFuzzwork(response={34: _aggregate(buy="42.0", sell="43.0")})
    async with SessionLocal() as session:
        result = await get_market_prices(
            session, fuzz, hub_id=HUB, type_ids=[34], now=NOW
        )
    assert fuzz.calls == 1
    assert result[0].buy_percentile == Decimal("42.0")  # refreshed value


async def test_outage_serves_stale_and_omits_unpriced():
    stale_at = NOW - timedelta(seconds=7200)
    await _seed_cache([_price_row(34, marker="1.0", fetched_at=stale_at)])
    fuzz = FakeFuzzwork(error=httpx.HTTPError("fuzzwork down"))
    async with SessionLocal() as session:
        result = await get_market_prices(
            session, fuzz, hub_id=HUB, type_ids=[34, 999], now=NOW
        )
    assert fuzz.calls == 1
    # 34 falls back to stale cache; 999 (never priced) is omitted, no exception.
    assert [r.type_id for r in result] == [34]
    assert result[0].buy_percentile == Decimal("1.0")
