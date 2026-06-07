from decimal import Decimal

import pytest

from app.data.db import SessionLocal
from app.data.repositories import appraisals as appraisals_repo
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import sde as sde_repo
from app.main import app
from app.plugins.fuzzwork import FuzzworkAggregate, FuzzworkSide, get_fuzzwork_client
from tests.helpers import CHAR_ID, CORP_ID, CeoEsi, MemberEsi, login, make_client


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _side(value: str) -> FuzzworkSide:
    return FuzzworkSide(
        weightedAverage=value, max=value, min=value, median=value,
        percentile=value, volume="1000", orderCount=10,
    )


def _empty_side() -> FuzzworkSide:
    return FuzzworkSide(
        weightedAverage="0", max="0", min="0", median="0",
        percentile="0", volume="0", orderCount=0,
    )


class FakeFuzzwork:
    def __init__(self, response: dict[int, FuzzworkAggregate]):
        self.response = response

    async def get_aggregates(self, *, station: int, type_ids: list[int]):
        return {t: self.response[t] for t in type_ids if t in self.response}


def _use_fuzzwork(response: dict[int, FuzzworkAggregate]) -> None:
    app.dependency_overrides[get_fuzzwork_client] = lambda: FakeFuzzwork(response)


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


async def test_appraisal_accepts_and_persists():
    await _seed_sde()
    # buy percentile 5.00 → default 90% buy → unit_price 4.50 → x1000 = 4500.00
    _use_fuzzwork({34: FuzzworkAggregate(buy=_side("5.00"), sell=_side("8.00"))})
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")

        resp = await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 34, "quantity": 1000}]}
        )
        assert resp.status_code == 201
        body = resp.json()
        assert len(body["public_id"]) == 12
        # Money round-trips through SQLite's REAL affinity (ADR-0020), so compare
        # numerically rather than by string scale.
        assert Decimal(body["accepted_total"]) == Decimal("4500.00")
        assert body["rejected_count"] == 0
        line = body["lines"][0]
        assert line["status"] == "accepted"
        assert line["basis"] == "buy"
        assert Decimal(line["percentage"]) == 90
        assert Decimal(line["unit_price"]) == Decimal("4.50")
        assert Decimal(line["line_total"]) == Decimal("4500.00")
        assert line["type_name"] == "Tritanium"

        # Re-fetch by public_id returns the same snapshot.
        again = await http.get(f"/api/v1/appraisals/{body['public_id']}")
        assert again.status_code == 200
        assert Decimal(again.json()["accepted_total"]) == Decimal("4500.00")


async def test_rule_overrides_default():
    await _seed_sde()
    _use_fuzzwork({34: FuzzworkAggregate(buy=_side("5.00"), sell=_side("10.00"))})
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        # A type rule: 50% sell → unit 10 * 50% = 5.00 → x10 = 50.00
        await http.put(
            "/api/v1/corporations/me/rules/type/34",
            json={"basis": "sell", "percentage": "50"},
        )
        resp = await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 34, "quantity": 10}]}
        )
        line = resp.json()["lines"][0]
        assert line["basis"] == "sell"
        assert Decimal(line["percentage"]) == 50
        assert Decimal(line["line_total"]) == Decimal("50.00")


async def test_market_group_rule_applies_in_appraisal():
    await _seed_sde()  # type 34 lives in market group 1
    _use_fuzzwork({34: FuzzworkAggregate(buy=_side("5.00"), sell=_side("10.00"))})
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        # A market-group rule on group 1 covers Tritanium: 50% sell.
        await http.put(
            "/api/v1/corporations/me/rules/market_group/1",
            json={"basis": "sell", "percentage": "50"},
        )
        resp = await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 34, "quantity": 2}]}
        )
        line = resp.json()["lines"][0]
        assert line["basis"] == "sell"
        assert Decimal(line["percentage"]) == 50
        assert Decimal(line["line_total"]) == Decimal("10.00")  # 10*50% = 5, x2


async def test_unknown_item_is_rejected():
    await _seed_sde()
    _use_fuzzwork({})
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        resp = await http.post(
            "/api/v1/appraisals",
            json={"items": [{"type_id": 999999, "quantity": 5}]},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert Decimal(body["accepted_total"]) == 0
        assert body["rejected_count"] == 1
        assert body["lines"][0]["status"] == "rejected"
        assert body["lines"][0]["reason"] == "Unknown item"


async def test_no_market_data_is_rejected():
    await _seed_sde()
    _use_fuzzwork({})  # type 34 is known but Fuzzwork returns nothing
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        resp = await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 34, "quantity": 5}]}
        )
        assert resp.json()["lines"][0]["reason"] == "No market data"


async def test_no_buy_orders_is_rejected():
    await _seed_sde()
    # buy side empty (no orders), sell present; default basis is buy → rejected
    _use_fuzzwork({34: FuzzworkAggregate(buy=_empty_side(), sell=_side("8.00"))})
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        resp = await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 34, "quantity": 5}]}
        )
        assert resp.json()["lines"][0]["reason"] == "No buy orders"


async def test_appraisal_from_paste_resolves_names():
    await _seed_sde()  # type 34 "Tritanium" in market group 1
    _use_fuzzwork({34: FuzzworkAggregate(buy=_side("5.00"), sell=_side("8.00"))})
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        resp = await http.post(
            "/api/v1/appraisals",
            # Name resolution is case-insensitive; the second line is unknown.
            json={"paste": "tritanium 1000\nNonexistent Item 5"},
        )
        assert resp.status_code == 201
        body = resp.json()
        lines = {(line["type_name"], line["status"], line["type_id"])
                 for line in body["lines"]}
        assert ("Tritanium", "accepted", 34) in lines
        assert ("Nonexistent Item", "rejected", None) in lines
        assert Decimal(body["accepted_total"]) == Decimal("4500.00")  # 5*90%*1000
        assert body["rejected_count"] == 1


async def test_appraisal_requires_items_or_paste():
    _use_fuzzwork({})  # so the fuzzwork dependency resolves; body validation fails first
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        resp = await http.post("/api/v1/appraisals", json={})
        assert resp.status_code == 422  # neither items nor paste


async def test_get_unknown_appraisal_404():
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        assert (await http.get("/api/v1/appraisals/doesnotexist")).status_code == 404


async def test_cross_corp_appraisal_is_404():
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        # An appraisal owned by a different corp.
        async with SessionLocal() as session:
            await corporations_repo.create_corporation(
                session, corporation_id=99999999, name="Other Corp",
                ceo_character_id=1, registered_by_character_id=1,
            )
            other = await appraisals_repo.create_appraisal(
                session,
                public_id="otherCorpXyz",
                corporation_id=99999999,
                created_by_character_id=1,
                market_hub_id=60003760,
                accepted_total=Decimal("0"),
                rejected_count=0,
                request_json={"items": []},
                lines=[],
            )
            await session.commit()
        resp = await http.get(f"/api/v1/appraisals/{other.public_id}")
        assert resp.status_code == 404


async def test_list_appraisals_returns_created():
    await _seed_sde()
    _use_fuzzwork({34: FuzzworkAggregate(buy=_side("5.00"), sell=_side("8.00"))})
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 34, "quantity": 1}]}
        )
        listed = await http.get("/api/v1/appraisals")
        assert listed.status_code == 200
        assert len(listed.json()) == 1


async def test_list_scope_member_sees_own_manager_sees_all():
    """A member lists only their own appraisals; a manager/CEO sees the corp's."""
    await _seed_sde()
    async with SessionLocal() as session:
        await corporations_repo.create_corporation(
            session, corporation_id=CORP_ID, name="Test Corp",
            ceo_character_id=99999, registered_by_character_id=99999,
        )
        await config_repo.upsert_config(
            session, corporation_id=CORP_ID, market_hub_id=60003760,
            default_basis="buy", default_percentage=90, aggregate_field="percentile",
        )
        # An appraisal owned by a different character in the same corp.
        await appraisals_repo.create_appraisal(
            session, public_id="teammateAppr", corporation_id=CORP_ID,
            created_by_character_id=777, market_hub_id=60003760,
            accepted_total=Decimal("0"), rejected_count=0,
            request_json={"items": []}, lines=[],
        )
        await session.commit()

    _use_fuzzwork({34: FuzzworkAggregate(buy=_side("5.00"), sell=_side("8.00"))})
    async with make_client(MemberEsi()) as http:
        me = await login(http)
        assert me["role"] == "member"
        await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 34, "quantity": 1}]}
        )
        mine = await http.get("/api/v1/appraisals")
        # Member sees only their own line, not the teammate's.
        assert [a["created_by_character_id"] for a in mine.json()] == [CHAR_ID]

    async with make_client(CeoEsi()) as http:
        await login(http)
        everyone = await http.get("/api/v1/appraisals")
        assert len(everyone.json()) == 2  # manager sees the whole corp
