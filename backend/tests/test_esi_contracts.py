"""EsiClient corp-contract reads (ADR-0037): item-exchange filtering, X-Pages pagination,
Decimal-safe price parsing (ADR-0020), and the 401/403 → CorporationContractsForbidden
mapping that lets the watcher skip a corp without flagging its token failed (#68)."""

import json
from decimal import Decimal

import httpx
import pytest

from app.plugins.esi import (
    CorporationContractsForbidden,
    EsiClient,
    OpenWindowForbidden,
)

_CONTRACTS_P1 = [
    {
        "contract_id": 1,
        "type": "item_exchange",
        "status": "outstanding",
        "title": "abcdefghijkl",
        "price": 1230000.55,  # JSON float — must survive as exact Decimal
        "start_location_id": 1035000000001,
        "issuer_id": 100,
        "acceptor_id": 0,
        "date_issued": "2026-06-18T10:00:00Z",
    },
    {
        # An auction in the same page — dropped (not item_exchange).
        "contract_id": 2,
        "type": "auction",
        "status": "outstanding",
        "title": "skip me",
        "date_issued": "2026-06-18T10:00:00Z",
    },
]
_CONTRACTS_P2 = [
    {
        "contract_id": 3,
        "type": "item_exchange",
        "status": "finished",
        "title": "second page",
        "price": 5,
        "date_issued": "2026-06-17T10:00:00Z",
        "date_completed": "2026-06-17T11:00:00Z",
    }
]


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_contracts_paginate_and_filter_item_exchange():
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        body = _CONTRACTS_P1 if page == "1" else _CONTRACTS_P2
        return httpx.Response(
            200, content=json.dumps(body), headers={"X-Pages": "2"}
        )

    async with _client(handler) as http:
        contracts = await EsiClient(http).get_corporation_contracts(98, "tok")

    # Both pages fetched; the auction is filtered out → ids 1 and 3 only.
    assert sorted(c.contract_id for c in contracts) == [1, 3]
    first = next(c for c in contracts if c.contract_id == 1)
    # Price parsed straight to Decimal — no binary-float drift.
    assert first.price == Decimal("1230000.55")
    assert first.start_location_id == 1035000000001


async def test_contracts_forbidden_maps_to_typed_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Forbidden"})

    async with _client(handler) as http:
        with pytest.raises(CorporationContractsForbidden):
            await EsiClient(http).get_corporation_contracts(98, "tok")


async def test_contract_items_paginate_and_sum_via_included_flag():
    p1 = [{"type_id": 34, "quantity": 60, "is_included": True}]
    p2 = [{"type_id": 34, "quantity": 40, "is_included": True}]

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        body = p1 if page == "1" else p2
        return httpx.Response(200, json=body, headers={"X-Pages": "2"})

    async with _client(handler) as http:
        items = await EsiClient(http).get_corporation_contract_items(98, 1, "tok")

    assert [(i.type_id, i.quantity, i.is_included) for i in items] == [
        (34, 60, True),
        (34, 40, True),
    ]


async def test_contract_items_forbidden_maps_to_typed_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async with _client(handler) as http:
        with pytest.raises(CorporationContractsForbidden):
            await EsiClient(http).get_corporation_contract_items(98, 1, "tok")


# --- open-window (ADR-0038) ---


async def test_open_contract_window_posts_with_contract_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["contract_id"] = request.url.params.get("contract_id")
        return httpx.Response(204)

    async with _client(handler) as http:
        await EsiClient(http).open_contract_window(777, "tok")
    assert seen == {"method": "POST", "contract_id": "777"}


async def test_open_contract_window_forbidden_maps_to_typed_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Forbidden"})

    async with _client(handler) as http:
        with pytest.raises(OpenWindowForbidden):
            await EsiClient(http).open_contract_window(777, "tok")
