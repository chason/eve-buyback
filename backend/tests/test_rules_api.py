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
        corp = await corporations_repo.create_corporation(
            session, eve_corporation_id=CORP_ID, name="Test Corp",
            ceo_character_id=99999, registered_by_character_id=99999,
        )
        await config_repo.upsert_config(
            session, corporation_id=corp.id, market_hub_id=60003760,
            default_basis="buy", default_percentage=90, aggregate_field="percentile",
        )
        await session.commit()


async def test_put_rule_lifecycle():
    await _seed_sde()
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")

        # PUT creates → 201.
        created = await http.put(
            "/api/v1/corporations/me/rules/type/34", json={"percentage": "95"}
        )
        assert created.status_code == 201
        body = created.json()
        # No surrogate id is exposed; the rule is identified by its target (ADR-0022).
        assert "id" not in body and "public_id" not in body
        assert (body["target_kind"], body["target_id"]) == ("type", 34)
        assert body["target_name"] == "Tritanium"  # resolved name, not just the id
        assert body["basis"] is None  # inherits config default

        listed = await http.get("/api/v1/corporations/me/rules")
        assert [(r["target_kind"], r["target_id"], r["target_name"]) for r in listed.json()] == [
            ("type", 34, "Tritanium")
        ]

        removed = await http.delete("/api/v1/corporations/me/rules/type/34")
        assert removed.status_code == 204
        assert (await http.get("/api/v1/corporations/me/rules")).json() == []


async def test_put_is_idempotent_upsert():
    await _seed_sde()
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")

        first = await http.put(
            "/api/v1/corporations/me/rules/type/34", json={"percentage": "95"}
        )
        assert first.status_code == 201

        # A second PUT replaces (no 409) → 200, and the new state wins.
        second = await http.put(
            "/api/v1/corporations/me/rules/type/34",
            json={"basis": "sell", "percentage": "50", "enabled": False},
        )
        assert second.status_code == 200
        assert second.json()["basis"] == "sell"
        assert second.json()["enabled"] is False
        assert Decimal(second.json()["percentage"]) == 50

        # Still exactly one rule for the target.
        listed = await http.get("/api/v1/corporations/me/rules")
        assert len(listed.json()) == 1


async def test_unknown_type_target_rejected():
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        resp = await http.put(
            "/api/v1/corporations/me/rules/type/999999", json={"percentage": "95"}
        )
        assert resp.status_code == 400


async def test_market_group_rule_put_and_validation():
    await _seed_sde()  # seeds market group 1
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")

        created = await http.put(
            "/api/v1/corporations/me/rules/market_group/1",
            json={"basis": "sell", "percentage": "80"},
        )
        assert created.status_code == 201
        assert created.json()["target_kind"] == "market_group"

        # An unknown market group is rejected.
        bad = await http.put(
            "/api/v1/corporations/me/rules/market_group/999999",
            json={"percentage": "80"},
        )
        assert bad.status_code == 400


async def test_delete_missing_rule_404():
    await _seed_sde()
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        # No rule exists for this target.
        resp = await http.delete("/api/v1/corporations/me/rules/type/34")
        assert resp.status_code == 404


async def test_bad_target_kind_in_path_422():
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        resp = await http.put(
            "/api/v1/corporations/me/rules/bananas/34", json={"percentage": "95"}
        )
        assert resp.status_code == 422  # Literal path param rejects the bad value


async def test_member_cannot_mutate_rules_but_can_list():
    await _seed_registered_corp()
    async with make_client(MemberEsi()) as http:
        await login(http)
        assert (await http.get("/api/v1/corporations/me/rules")).status_code == 200
        resp = await http.put(
            "/api/v1/corporations/me/rules/type/34", json={"percentage": "95"}
        )
        assert resp.status_code == 403
