"""Gateway to the Fuzzwork market-aggregates API (ADR-0006).

A plugin (outside-API gateway): it speaks HTTP to Fuzzwork and always hands back
Pydantic models, never raw JSON. Pricing/caching policy lives in the application
layer, not here.
"""

from decimal import Decimal

import httpx
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from app.domain.aggregates import BuySellAggregate

FUZZWORK_AGGREGATES_URL = "https://market.fuzzwork.co.uk/aggregates/"

# Fuzzwork accepts a large CSV, but keep requests modest and merge the results.
_CHUNK = 200


class FuzzworkSide(BaseModel):
    """One side (buy or sell) of a Fuzzwork aggregate, satisfying the domain
    `AggregateSide` protocol (#19). Fuzzwork returns camelCase keys and stringy numbers;
    the snake_case fields (aliased to the wire keys) match the protocol, and parsing
    straight to `Decimal` preserves the exact wire digits (no float round-trip,
    ADR-0020)."""

    model_config = ConfigDict(populate_by_name=True)

    weighted_average: Decimal = Field(alias="weightedAverage")
    max: Decimal
    min: Decimal
    median: Decimal
    percentile: Decimal
    volume: Decimal
    order_count: int = Field(alias="orderCount")


class FuzzworkAggregate(BaseModel):
    """A Fuzzwork buy/sell aggregate, satisfying the domain `BuySellAggregate`
    protocol (#19)."""

    buy: FuzzworkSide
    sell: FuzzworkSide


class FuzzworkClient:
    """Thin async wrapper over the shared httpx client."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_aggregates(
        self, *, station: str, type_ids: list[int]
    ) -> dict[int, BuySellAggregate]:
        """Fetch buy/sell aggregates for `type_ids` at one station, keyed by type id.
        Returned as `BuySellAggregate` (the protocol) so the source is interchangeable
        with ESI's at the market seam (#19)."""
        result: dict[int, BuySellAggregate] = {}
        for start in range(0, len(type_ids), _CHUNK):
            chunk = type_ids[start : start + _CHUNK]
            resp = await self._client.get(
                FUZZWORK_AGGREGATES_URL,
                params={"station": station, "types": ",".join(map(str, chunk))},
            )
            resp.raise_for_status()
            for key, value in resp.json().items():
                result[int(key)] = FuzzworkAggregate.model_validate(value)
        return result


def get_fuzzwork_client(request: Request) -> FuzzworkClient:
    return FuzzworkClient(request.app.state.http)
