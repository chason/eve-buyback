"""Accepted buyback drop-off locations (ADR-0030): manager-gated CRUD, NPC-station
SDE resolution, structure validation, idempotency."""

import pytest

from app.data.db import SessionLocal
from app.data.repositories import sde as sde_repo
from app.main import app
from tests.helpers import CeoEsi, MemberEsi, login, make_client


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _seed_station() -> None:
    async with SessionLocal() as session:
        await sde_repo.bulk_upsert_stations(
            session,
            [
                {
                    "station_id": 60012345,
                    "name": "Korsiki VII - Moon 1 - Expert Distribution Warehouse",
                    "system_name": "Korsiki",
                    "region_id": 10000033,
                }
            ],
        )
        await session.commit()


async def _register(http) -> None:
    await login(http)
    await http.post("/api/v1/corporations")


async def test_add_npc_station_resolves_name_from_sde():
    await _seed_station()
    async with make_client(CeoEsi()) as http:
        await _register(http)
        resp = await http.post(
            "/api/v1/corporations/me/locations",
            json={"location_id": "60012345", "kind": "npc_station"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["kind"] == "npc_station"
        assert body["name"] == (
            "Korsiki - Korsiki VII - Moon 1 - Expert Distribution Warehouse"
        )
        assert body["system_name"] == "Korsiki"

        listed = await http.get("/api/v1/corporations/me/locations")
        assert [loc["location_id"] for loc in listed.json()] == ["60012345"]


async def test_add_structure_uses_client_name():
    async with make_client(CeoEsi()) as http:
        await _register(http)
        resp = await http.post(
            "/api/v1/corporations/me/locations",
            json={
                "location_id": "1035000000001",
                "kind": "structure",
                "name": "1DQ1-A - Palace",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["kind"] == "structure"
        assert body["name"] == "1DQ1-A - Palace"  # no SDE — trusts the search name
        assert body["system_name"] is None


async def test_add_unknown_station_is_rejected():
    async with make_client(CeoEsi()) as http:
        await _register(http)
        resp = await http.post(
            "/api/v1/corporations/me/locations",
            json={"location_id": "60099999", "kind": "npc_station"},
        )
        assert resp.status_code == 422


async def test_add_non_numeric_location_is_rejected():
    # A non-numeric id can't be a station/structure and could inject an ESI path.
    async with make_client(CeoEsi()) as http:
        await _register(http)
        resp = await http.post(
            "/api/v1/corporations/me/locations",
            json={"location_id": "1/../x", "kind": "structure", "name": "x"},
        )
        assert resp.status_code == 422


async def test_add_is_idempotent():
    await _seed_station()
    async with make_client(CeoEsi()) as http:
        await _register(http)
        payload = {"location_id": "60012345", "kind": "npc_station"}
        assert (
            await http.post("/api/v1/corporations/me/locations", json=payload)
        ).status_code == 201
        assert (
            await http.post("/api/v1/corporations/me/locations", json=payload)
        ).status_code == 201
        listed = await http.get("/api/v1/corporations/me/locations")
        assert len(listed.json()) == 1  # added once


async def test_remove_location():
    async with make_client(CeoEsi()) as http:
        await _register(http)
        await http.post(
            "/api/v1/corporations/me/locations",
            json={"location_id": "1035000000001", "kind": "structure", "name": "S"},
        )
        resp = await http.delete(
            "/api/v1/corporations/me/locations/1035000000001"
        )
        assert resp.status_code == 204
        assert (await http.get("/api/v1/corporations/me/locations")).json() == []
        # Removing again is a 404.
        gone = await http.delete("/api/v1/corporations/me/locations/1035000000001")
        assert gone.status_code == 404


async def test_member_can_list_but_not_change():
    async with make_client(CeoEsi()) as http:
        await _register(http)
        await http.post(
            "/api/v1/corporations/me/locations",
            json={"location_id": "1035000000001", "kind": "structure", "name": "S"},
        )

    async with make_client(MemberEsi()) as http:
        await login(http)
        # A member may read the list (they pick one when appraising)…
        listed = await http.get("/api/v1/corporations/me/locations")
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        # …but cannot add or remove.
        add = await http.post(
            "/api/v1/corporations/me/locations",
            json={"location_id": "60003760", "kind": "npc_station"},
        )
        assert add.status_code == 403
        rm = await http.delete(
            "/api/v1/corporations/me/locations/1035000000001"
        )
        assert rm.status_code == 403
