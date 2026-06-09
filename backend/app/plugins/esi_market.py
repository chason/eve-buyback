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


class StructureAccessDenied(Exception):
    """ESI returned 403 for a structure market — the character lost docking/market
    access. The market layer degrades gracefully (serves cache) rather than failing."""


class EsiMarketClient:
    """Thin async wrapper over the shared httpx client for ESI market endpoints."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_region_aggregates(
        self, *, region_id: int, station_id: str, type_ids: list[int]
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
                if str(o.get("location_id")) == station_id
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

    async def get_structure_aggregates(
        self, *, structure_id: str, type_ids: list[int], access_token: str
    ) -> dict[int, OrderBookAggregate]:
        """Aggregate buy/sell at a player structure (ADR-0029). The structure market
        endpoint returns ALL orders (not type-filterable), paginated and authenticated
        — fetch the whole book once, index by type, aggregate the requested types.
        A 403 (lost docking/market access) raises `StructureAccessDenied`."""
        orders_by_type = await self._structure_orders(structure_id, access_token)
        return {
            type_id: aggregate_orders(orders_by_type.get(type_id, []))
            for type_id in type_ids
        }

    async def _structure_orders(
        self, structure_id: int, access_token: str
    ) -> dict[int, list[RawOrder]]:
        url = f"{ESI_BASE}/markets/structures/{structure_id}/"
        headers = {"Authorization": f"Bearer {access_token}"}
        by_type: dict[int, list[RawOrder]] = {}
        page = 1
        while True:
            resp = await self._client.get(
                url,
                params={"page": page, "datasource": "tranquility"},
                headers=headers,
            )
            if resp.status_code == 403:
                raise StructureAccessDenied()
            await self._respect_error_limit(resp)
            resp.raise_for_status()
            for order in json.loads(resp.text, parse_float=Decimal):
                by_type.setdefault(int(order["type_id"]), []).append(
                    RawOrder(
                        price=Decimal(order["price"]),
                        volume_remain=int(order["volume_remain"]),
                        is_buy_order=bool(order["is_buy_order"]),
                    )
                )
            if page >= int(resp.headers.get("X-Pages", "1")):
                break
            page += 1
        return by_type

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
