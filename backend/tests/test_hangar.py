"""#154 / ADR-0044: reading the buyback hangar — the assets ESI read (pagination,
403 mapping), the pure hangar count, the hangar config use cases, and the API
(gates, unknown-location 404, division bounds)."""

import json

import httpx
import pytest

from app.application import hangar as hangar_app
from app.application.errors import HangarLocationUnknown
from app.data.db import SessionLocal
from app.data.repositories import buyback_locations as locations_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.domain.hangar import AssetStack, HangarKey, division_flag, hangar_counts
from app.main import app
from app.plugins.esi import CorporationAssetsForbidden, EsiClient
from tests.helpers import CHAR_ID, CORP_ID, CeoEsi, login, make_client

JITA = "60003760"
STRUCTURE = "1035000000001"


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# --- ESI plugin -------------------------------------------------------------------


async def test_assets_paginate():
    p1 = [{"item_id": 1, "type_id": 34, "quantity": 100,
           "location_id": 60003760, "location_flag": "CorpSAG2"}]
    p2 = [{"item_id": 2, "type_id": 35, "quantity": 50,
           "location_id": 60003760, "location_flag": "CorpSAG2",
           "is_singleton": True}]

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        body = p1 if page == "1" else p2
        return httpx.Response(200, content=json.dumps(body), headers={"X-Pages": "2"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assets = await EsiClient(http).get_corporation_assets(98, "tok")

    assert [(a.item_id, a.type_id, a.quantity, a.is_singleton) for a in assets] == [
        (1, 34, 100, False),
        (2, 35, 50, True),
    ]


async def test_assets_forbidden_maps_to_typed_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Forbidden"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(CorporationAssetsForbidden):
            await EsiClient(http).get_corporation_assets(98, "tok")


# --- domain count -------------------------------------------------------------------


_NEXT_ITEM_ID = iter(range(9_000_000, 10_000_000))


def _stack(
    type_id, qty, *, location=60003760, flag="CorpSAG2", item_id=None
) -> AssetStack:
    return AssetStack(
        item_id=item_id if item_id is not None else next(_NEXT_ITEM_ID),
        type_id=type_id,
        quantity=qty,
        location_id=location,
        location_flag=flag,
    )


def test_division_flag_maps_to_corpsag():
    assert division_flag(1) == "CorpSAG1"
    assert division_flag(7) == "CorpSAG7"


def test_hangar_counts_filters_and_sums_per_location_and_type():
    hangars = [HangarKey(location_id=JITA, division=2)]
    counts = hangar_counts(
        [
            _stack(34, 100),
            _stack(34, 50),  # second stack of the same type sums
            _stack(35, 10),
            _stack(34, 999, flag="CorpSAG1"),  # other division — not buyback
            _stack(34, 999, location=1035000000001),  # other location
            # Inside a container that is NOT in a marked hangar → excluded.
            _stack(34, 999, location=1_000_000_000_555, flag="Unlocked"),
        ],
        hangars,
    )
    assert counts == {(JITA, 34): 150, (JITA, 35): 10}


def test_hangar_counts_include_container_contents_recursively():
    hangars = [HangarKey(location_id=JITA, division=2)]
    # A station container in the marked hangar, holding minerals and a nested
    # container that holds more — all of it is physically in the buyback hangar.
    outer = _stack(17366, 1, item_id=5001)  # Station Container, type 17366
    inner = _stack(3467, 1, location=5001, flag="Unlocked", item_id=5002)
    counts = hangar_counts(
        [
            outer,
            _stack(34, 100, location=5001, flag="Unlocked"),
            inner,
            _stack(35, 40, location=5002, flag="Unlocked"),
            _stack(34, 25),  # loose in the hangar alongside the container
        ],
        hangars,
    )
    # Contents attribute to the STATION (the ledger's granularity), containers count
    # as physical items themselves, and nesting resolves transitively.
    assert counts == {
        (JITA, 34): 125,
        (JITA, 35): 40,
        (JITA, 17366): 1,
        (JITA, 3467): 1,
    }


def test_hangar_counts_spans_multiple_hangars():
    hangars = [
        HangarKey(location_id=JITA, division=2),
        HangarKey(location_id=STRUCTURE, division=5),
    ]
    counts = hangar_counts(
        [
            _stack(34, 100),
            _stack(34, 40, location=int(STRUCTURE), flag="CorpSAG5"),
        ],
        hangars,
    )
    # Counts stay per (location, type) — the reconciliation is per hangar location.
    assert counts == {(JITA, 34): 100, (STRUCTURE, 34): 40}


def test_hangar_counts_empty_config_counts_nothing():
    assert hangar_counts([_stack(34, 100)], []) == {}


# --- config use cases + API ----------------------------------------------------------


async def _seed_corp(*, entitled: bool = True, with_location: bool = True):
    async with SessionLocal() as session:
        corp = await corporations_repo.create_corporation(
            session,
            eve_corporation_id=CORP_ID,
            name="Test Corp",
            ceo_character_id=CHAR_ID,
            registered_by_character_id=CHAR_ID,
        )
        if entitled:
            await entitlements_repo.upsert(
                session, corporation_id=corp.id, feature="accounting",
                source="admin", expires_at=None,
            )
        if with_location:
            await locations_repo.add(
                session, corp.id, kind="npc_station", location_id=JITA,
                name="Jita IV - Moon 4", system_name="Jita",
            )
        await session.commit()
        return corp.id


async def test_add_list_remove_round_trip():
    await _seed_corp()
    async with make_client(CeoEsi()) as http:
        await login(http)

        resp = await http.post(
            "/api/v1/corporations/me/accounting/hangars",
            json={"location_id": JITA, "division": 2},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body == {
            "location_id": JITA,
            "location_name": "Jita IV - Moon 4",  # snapshotted from the drop-off
            "division": 2,
        }

        # Re-adding is idempotent (201 with the same row, no duplicate).
        resp = await http.post(
            "/api/v1/corporations/me/accounting/hangars",
            json={"location_id": JITA, "division": 2},
        )
        assert resp.status_code == 201

        resp = await http.get("/api/v1/corporations/me/accounting/hangars")
        assert [h["division"] for h in resp.json()] == [2]

        resp = await http.delete(
            f"/api/v1/corporations/me/accounting/hangars/{JITA}/2"
        )
        assert resp.status_code == 204
        resp = await http.get("/api/v1/corporations/me/accounting/hangars")
        assert resp.json() == []


async def test_add_rejects_a_location_that_is_not_a_drop_off():
    await _seed_corp(with_location=False)
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.post(
            "/api/v1/corporations/me/accounting/hangars",
            json={"location_id": JITA, "division": 2},
        )
    assert resp.status_code == 404
    assert "drop-off locations" in resp.json()["detail"]


async def test_add_rejects_an_out_of_range_division():
    await _seed_corp()
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.post(
            "/api/v1/corporations/me/accounting/hangars",
            json={"location_id": JITA, "division": 8},
        )
    assert resp.status_code == 422


async def test_hangars_402_without_entitlement():
    await _seed_corp(entitled=False)
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.get("/api/v1/corporations/me/accounting/hangars")
    assert resp.status_code == 402


# --- fetch_hangar_counts (the read #155 consumes) -------------------------------------


class _AssetsEsi:
    def __init__(self, assets):
        self._assets = assets
        self.calls = 0

    async def get_corporation_assets(self, corporation_id, access_token):
        self.calls += 1
        if isinstance(self._assets, Exception):
            raise self._assets
        return self._assets


class _FakeSso:
    async def refresh_access_token(self, refresh_token):
        from app.plugins.sso import OAuthToken

        return OAuthToken(access_token="fresh", refresh_token=refresh_token)


async def test_fetch_counts_no_hangars_makes_no_esi_call():
    await _seed_corp()
    esi = _AssetsEsi([])
    async with SessionLocal() as session:
        counts = await hangar_app.fetch_hangar_counts(
            session, _FakeSso(), esi, corporation_eve_id=CORP_ID, cipher=None
        )
    assert counts == {}
    assert esi.calls == 0


async def test_add_hangar_rejects_unknown_location_at_use_case_level():
    await _seed_corp(with_location=False)
    async with SessionLocal() as session:
        with pytest.raises(HangarLocationUnknown):
            await hangar_app.add_hangar(
                session, corporation_eve_id=CORP_ID,
                location_id=JITA, division=2,
            )
