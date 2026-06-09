"""Gateway to EVE ESI market data (ADR-0028), used when the configured hub isn't a
Fuzzwork hub.

A plugin (outside-API gateway): speaks HTTP to ESI, parses raw orders, and hands
back `domain.aggregates.OrderBookAggregate`s — the same shape Fuzzwork yields — so
the market use case is source-agnostic. Order prices are parsed JSON-number →
`Decimal` directly (`parse_float=Decimal`) to avoid any float round-trip (ADR-0020).

Phase A covers public NPC stations via region orders. Structure markets (auth) land
in a later phase.
"""

import asyncio
import json
import logging
from decimal import Decimal

import httpx
from fastapi import Request

from app.config import get_settings
from app.domain.aggregates import OrderBookAggregate, RawOrder, aggregate_orders

ESI_BASE = "https://esi.evetech.net/latest"
# Back off when ESI's sliding error budget drops to/below this many remaining.
_ERROR_LIMIT_FLOOR = 5

log = logging.getLogger(__name__)


class EsiMarketClient:
    """Thin async wrapper over the shared httpx client for ESI market endpoints."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def resolve_station(self, station_id: int) -> tuple[int, str]:
        """Resolve an NPC station id to `(region_id, station_name)` — station→system
        →constellation→region. Called once when a manager sets the hub, not on the
        hot path. Raises `httpx.HTTPStatusError` for an unknown station."""
        station = await self._get(f"{ESI_BASE}/universe/stations/{station_id}/")
        system = await self._get(
            f"{ESI_BASE}/universe/systems/{station['system_id']}/"
        )
        constellation = await self._get(
            f"{ESI_BASE}/universe/constellations/{system['constellation_id']}/"
        )
        return int(constellation["region_id"]), str(station["name"])

    async def get_region_aggregates(
        self, *, region_id: int, station_id: int, type_ids: list[int]
    ) -> dict[int, OrderBookAggregate]:
        """Aggregate buy/sell at one NPC station from its region's order book, keyed
        by type id. One paginated request per type, fanned out under a concurrency
        cap; a single type's failure is logged and skipped (the cache simply keeps
        missing it), not fatal."""
        sem = asyncio.Semaphore(get_settings().esi_market_concurrency)

        async def one(type_id: int) -> tuple[int, OrderBookAggregate]:
            async with sem:
                orders = await self._region_orders_for_type(region_id, type_id)
            at_station = [
                RawOrder(
                    price=Decimal(o["price"]),
                    volume_remain=int(o["volume_remain"]),
                    is_buy_order=bool(o["is_buy_order"]),
                )
                for o in orders
                if o.get("location_id") == station_id
            ]
            return type_id, aggregate_orders(at_station)

        results = await asyncio.gather(
            *(one(t) for t in type_ids), return_exceptions=True
        )
        out: dict[int, OrderBookAggregate] = {}
        for result in results:
            if isinstance(result, Exception):
                log.warning("ESI region-orders fetch failed for a type: %r", result)
                continue
            type_id, aggregate = result
            out[type_id] = aggregate
        return out

    async def _region_orders_for_type(
        self, region_id: int, type_id: int
    ) -> list[dict]:
        url = f"{ESI_BASE}/markets/{region_id}/orders/"
        params = {
            "type_id": type_id,
            "order_type": "all",
            "datasource": "tranquility",
        }
        first = await self._client.get(url, params={**params, "page": 1})
        await self._respect_error_limit(first)
        first.raise_for_status()
        orders = json.loads(first.text, parse_float=Decimal)
        pages = int(first.headers.get("X-Pages", "1"))
        for page in range(2, pages + 1):
            resp = await self._client.get(url, params={**params, "page": page})
            await self._respect_error_limit(resp)
            resp.raise_for_status()
            orders.extend(json.loads(resp.text, parse_float=Decimal))
        return orders

    async def _get(self, url: str) -> dict:
        resp = await self._client.get(url, params={"datasource": "tranquility"})
        await self._respect_error_limit(resp)
        resp.raise_for_status()
        return resp.json()

    async def _respect_error_limit(self, resp: httpx.Response) -> None:
        """Politely back off when ESI's error budget is nearly spent (ESI conventions)."""
        remain = resp.headers.get("X-Esi-Error-Limit-Remain")
        if remain is not None and int(remain) <= _ERROR_LIMIT_FLOOR:
            reset = int(resp.headers.get("X-Esi-Error-Limit-Reset", "1"))
            log.warning(
                "ESI error budget low (%s remaining); backing off %ss", remain, reset
            )
            await asyncio.sleep(reset)


def get_esi_market_client(request: Request) -> EsiMarketClient:
    return EsiMarketClient(request.app.state.http)
