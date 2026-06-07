from decimal import Decimal

import pytest

from app.data.db import SessionLocal
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import sde as sde_repo
from app.main import app
from tests.helpers import CORP_ID, CeoEsi, MemberEsi, login, make_client


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _seed_sde() -> None:
    async with SessionLocal() as session:
        await sde_repo.bulk_upsert_market_groups(
            session, [{"market_group_id": 1, "parent_id": None, "name": "Ore"}]
        )
        await sde_repo.bulk_upsert_types(
            session,
            [{"type_id": 34, "name": "Tritanium", "group_id": 18,
              "market_group_id": 1, "volume": 0.01, "published": True}],
        )
        await session.commit()


async def _seed_registered_corp() -> None:
    async with SessionLocal() as session:
        await corporations_repo.create_corporation(
            session, corporation_id=CORP_ID, name="Test Corp",
            ceo_character_id=99999, registered_by_character_id=99999,
        )
        await config_repo.upsert_config(
            session, corporation_id=CORP_ID, market_hub_id=60003760,
            default_basis="buy", default_percentage=90, aggregate_field="percentile",
        )
        await session.commit()


async def test_rule_crud_lifecycle():
    await _seed_sde()
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")

        created = await http.post(
            "/api/v1/corporations/me/rules",
            json={"target_kind": "type", "target_id": 34, "percentage": "95"},
        )
        assert created.status_code == 201
        rule_id = created.json()["id"]
        assert created.json()["basis"] is None  # inherits config default

        listed = await http.get("/api/v1/corporations/me/rules")
        assert [r["id"] for r in listed.json()] == [rule_id]

        patched = await http.patch(
            f"/api/v1/corporations/me/rules/{rule_id}",
            json={"basis": "sell", "enabled": False},
        )
        assert patched.status_code == 200
        assert patched.json()["basis"] == "sell"
        assert patched.json()["enabled"] is False
        assert Decimal(patched.json()["percentage"]) == 95  # unchanged

        removed = await http.delete(f"/api/v1/corporations/me/rules/{rule_id}")
        assert removed.status_code == 204
        assert (await http.get("/api/v1/corporations/me/rules")).json() == []


async def test_duplicate_target_conflicts():
    await _seed_sde()
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        body = {"target_kind": "type", "target_id": 34, "percentage": "95"}
        assert (await http.post("/api/v1/corporations/me/rules", json=body)).status_code == 201
        assert (await http.post("/api/v1/corporations/me/rules", json=body)).status_code == 409


async def test_unknown_type_target_rejected():
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        resp = await http.post(
            "/api/v1/corporations/me/rules",
            json={"target_kind": "type", "target_id": 999999, "percentage": "95"},
        )
        assert resp.status_code == 400


async def test_market_group_rule_crud_and_validation():
    await _seed_sde()  # seeds market group 1
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")

        # A valid market-group rule.
        created = await http.post(
            "/api/v1/corporations/me/rules",
            json={"target_kind": "market_group", "target_id": 1,
                  "basis": "sell", "percentage": "80"},
        )
        assert created.status_code == 201
        assert created.json()["target_kind"] == "market_group"

        # An unknown market group is rejected.
        bad = await http.post(
            "/api/v1/corporations/me/rules",
            json={"target_kind": "market_group", "target_id": 999999,
                  "percentage": "80"},
        )
        assert bad.status_code == 400


async def test_patch_missing_rule_404():
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        resp = await http.patch(
            "/api/v1/corporations/me/rules/4242", json={"percentage": "50"}
        )
        assert resp.status_code == 404


async def test_member_cannot_mutate_rules_but_can_list():
    await _seed_registered_corp()
    async with make_client(MemberEsi()) as http:
        await login(http)
        assert (await http.get("/api/v1/corporations/me/rules")).status_code == 200
        resp = await http.post(
            "/api/v1/corporations/me/rules",
            json={"target_kind": "type", "target_id": 34, "percentage": "95"},
        )
        assert resp.status_code == 403
