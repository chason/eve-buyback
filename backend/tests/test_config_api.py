from decimal import Decimal

import pytest

from app.data.db import SessionLocal
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import corporations as corporations_repo
from app.main import app
from tests.helpers import CORP_ID, CeoEsi, MemberEsi, login, make_client


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _seed_registered_corp() -> None:
    async with SessionLocal() as session:
        await corporations_repo.create_corporation(
            session,
            corporation_id=CORP_ID,
            name="Test Corp",
            ceo_character_id=99999,
            registered_by_character_id=99999,
        )
        await config_repo.upsert_config(
            session,
            corporation_id=CORP_ID,
            market_hub_id=60003760,
            default_basis="buy",
            default_percentage=90,
            aggregate_field="percentile",
        )
        await session.commit()


async def test_default_config_created_on_registration():
    async with make_client(CeoEsi()) as http:
        await login(http)
        assert (await http.post("/api/v1/corporations")).status_code == 201

        resp = await http.get("/api/v1/corporations/me/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["default_basis"] == "buy"
        assert Decimal(body["default_percentage"]) == 90
        assert body["aggregate_field"] == "percentile"
        assert body["market_hub_id"] == 60003760


async def test_manager_can_update_config():
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")

        resp = await http.put(
            "/api/v1/corporations/me/config",
            json={
                "market_hub_id": 60003760,
                "default_basis": "split",
                "default_percentage": "85.5",
                "aggregate_field": "weighted_average",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["default_basis"] == "split"

        after = await http.get("/api/v1/corporations/me/config")
        assert Decimal(after.json()["default_percentage"]) == Decimal("85.5")


async def test_member_cannot_update_config_but_can_read():
    await _seed_registered_corp()
    async with make_client(MemberEsi()) as http:
        me = await login(http)
        assert me["role"] == "member"

        assert (await http.get("/api/v1/corporations/me/config")).status_code == 200

        resp = await http.put(
            "/api/v1/corporations/me/config",
            json={
                "market_hub_id": 60003760,
                "default_basis": "buy",
                "default_percentage": "50",
                "aggregate_field": "percentile",
            },
        )
        assert resp.status_code == 403


async def test_get_config_lazily_creates_default_when_missing():
    # A registered corp with no config row (e.g. registered before configs existed).
    async with SessionLocal() as session:
        await corporations_repo.create_corporation(
            session, corporation_id=CORP_ID, name="Test Corp",
            ceo_character_id=99999, registered_by_character_id=99999,
        )
        await session.commit()

    async with make_client(MemberEsi()) as http:
        await login(http)
        resp = await http.get("/api/v1/corporations/me/config")
        assert resp.status_code == 200
        assert resp.json()["default_basis"] == "buy"  # default, lazily created

    # The lazily-created config is now persisted.
    async with SessionLocal() as session:
        assert await config_repo.get_config(session, CORP_ID) is not None


async def test_config_rejects_bad_basis():
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        resp = await http.put(
            "/api/v1/corporations/me/config",
            json={
                "market_hub_id": 60003760,
                "default_basis": "bananas",
                "default_percentage": "90",
                "aggregate_field": "percentile",
            },
        )
        assert resp.status_code == 422  # Literal rejects the bad enum
