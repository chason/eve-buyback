from decimal import Decimal

import pytest

from app.data.db import SessionLocal
from app.data.repositories import appraisals as appraisals_repo
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import pricing_rules as rules_repo
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
    """Canned aggregates. `response` serves any station; `by_station` (hub_id str →
    response dict) overrides per hub for multi-hub tests (ADR-0031). `stations`
    records which hubs were fetched."""

    def __init__(
        self,
        response: dict[int, FuzzworkAggregate] | None = None,
        by_station: dict[str, dict[int, FuzzworkAggregate]] | None = None,
    ):
        self.response = response or {}
        self.by_station = by_station
        self.stations: list[str] = []

    async def get_aggregates(self, *, station: str, type_ids: list[int]):
        self.stations.append(str(station))
        src = (
            self.by_station.get(str(station), {})
            if self.by_station is not None
            else self.response
        )
        return {t: src[t] for t in type_ids if t in src}


def _use_fuzzwork(response: dict[int, FuzzworkAggregate]) -> FakeFuzzwork:
    fake = FakeFuzzwork(response)
    app.dependency_overrides[get_fuzzwork_client] = lambda: fake
    return fake


def _use_fuzzwork_by_station(
    by_station: dict[str, dict[int, FuzzworkAggregate]],
) -> FakeFuzzwork:
    fake = FakeFuzzwork(by_station=by_station)
    app.dependency_overrides[get_fuzzwork_client] = lambda: fake
    return fake


async def _seed_sde() -> None:
    async with SessionLocal() as session:
        await sde_repo.bulk_upsert_market_groups(
            session, [{"market_group_id": 1, "parent_id": None, "name": "Ore"}]
        )
        await sde_repo.bulk_upsert_types(
            session,
            [{"type_id": 34, "name": "Tritanium", "group_id": 18,
              "market_group_id": 1, "volume": 0.01, "published": True},
             {"type_id": 35, "name": "Pyerite", "group_id": 18,
              "market_group_id": 1, "volume": 0.01, "published": True}],
        )
        await session.commit()


async def _seed_ore() -> None:
    """Veldspar (ore, category 25, 100-unit refine batch → 400 Tritanium) + the
    Tritanium it reprocesses to."""
    async with SessionLocal() as session:
        await sde_repo.bulk_upsert_market_groups(
            session, [{"market_group_id": 1, "parent_id": None, "name": "Ore"}]
        )
        await sde_repo.bulk_upsert_types(session, [
            {"type_id": 1230, "name": "Veldspar", "group_id": 462, "category_id": 25,
             "market_group_id": 1, "volume": 0.1, "portion_size": 100,
             "published": True},
            {"type_id": 34, "name": "Tritanium", "group_id": 18, "category_id": 4,
             "market_group_id": 1, "volume": 0.01, "portion_size": 1,
             "published": True},
        ])
        await sde_repo.bulk_upsert_type_materials(
            session, [{"type_id": 1230, "material_type_id": 34, "quantity": 400}]
        )
        await session.commit()


def _use_ore_fuzzwork() -> None:
    # Tritanium buys at 100.00; Veldspar's own price is 2.00 (leftover/direct only).
    _use_fuzzwork({
        34: FuzzworkAggregate(buy=_side("100.00"), sell=_side("100.00")),
        1230: FuzzworkAggregate(buy=_side("2.00"), sell=_side("2.00")),
    })


async def test_appraisal_reprocess_rule_prices_by_minerals():
    await _seed_ore()
    _use_ore_fuzzwork()
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        await http.put(
            "/api/v1/corporations/me/rules/type/1230",
            json={"percentage": "100", "reprocess": True},
        )
        resp = await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 1230, "quantity": 100}]}
        )
        line = resp.json()["lines"][0]
        assert line["status"] == "accepted"
        # 1 batch = 400 Trit × 0.9063 yield × 100.00 ISK = 36252.00.
        assert Decimal(line["line_total"]) == Decimal("36252.00")
        # The per-line breakdown names the minerals and their market value.
        bd = line["reprocess"]
        assert bd["leftover_units"] == 0
        assert len(bd["minerals"]) == 1
        m = bd["minerals"][0]
        assert m["type_name"] == "Tritanium"
        assert Decimal(m["quantity"]) == Decimal("400") * Decimal("0.9063")  # 362.52
        assert Decimal(m["value"]) == Decimal("36252.00")


async def test_appraisal_reprocess_sub_batch_uses_ore_price():
    await _seed_ore()
    _use_ore_fuzzwork()
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        await http.put(
            "/api/v1/corporations/me/rules/type/1230",
            json={"percentage": "100", "reprocess": True},
        )
        # 50 < one 100-unit batch → entirely the ore's own buy price (50 × 2.00).
        resp = await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 1230, "quantity": 50}]}
        )
        assert Decimal(resp.json()["lines"][0]["line_total"]) == Decimal("100.00")


async def test_appraisal_whitelist_mode_rejects_unruled_items():
    await _seed_sde()
    _use_fuzzwork({34: FuzzworkAggregate(buy=_side("5.00"), sell=_side("8.00"))})
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        # Flip the corp to whitelist-only buyback.
        await http.put("/api/v1/corporations/me/config", json={
            "market_hub_id": "60003760", "default_basis": "buy",
            "default_percentage": "90", "aggregate_field": "percentile",
            "default_accepted": False,
        })
        # No rule for Tritanium → rejected by the default.
        resp = await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 34, "quantity": 1000}]}
        )
        assert resp.json()["lines"][0]["status"] == "rejected"
        assert resp.json()["lines"][0]["reason"] == "Not accepted"

        # Add an accepting rule → now it's bought.
        await http.put(
            "/api/v1/corporations/me/rules/type/34", json={"percentage": "90"}
        )
        resp2 = await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 34, "quantity": 1000}]}
        )
        assert resp2.json()["lines"][0]["status"] == "accepted"


async def test_appraisal_not_accepted_rule_rejects_item():
    await _seed_sde()
    _use_fuzzwork({34: FuzzworkAggregate(buy=_side("5.00"), sell=_side("8.00"))})
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        # Blacklist Tritanium even though it has a market price.
        await http.put(
            "/api/v1/corporations/me/rules/type/34",
            json={"percentage": "0", "accepted": False},
        )
        resp = await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 34, "quantity": 1000}]}
        )
        line = resp.json()["lines"][0]
        assert line["status"] == "rejected"
        assert line["reason"] == "Not accepted"
        assert Decimal(resp.json()["accepted_total"]) == 0


async def test_appraisal_compressed_only_rejects_uncompressed_ore():
    # Raw + compressed Veldspar in one ore market group; a compressed-only rule on
    # the group accepts the compressed one and rejects the raw one.
    async with SessionLocal() as session:
        await sde_repo.bulk_upsert_market_groups(
            session, [{"market_group_id": 1, "parent_id": None, "name": "Ore"}]
        )
        await sde_repo.bulk_upsert_types(session, [
            {"type_id": 1230, "name": "Veldspar", "group_id": 462, "category_id": 25,
             "market_group_id": 1, "volume": 0.1, "portion_size": 100,
             "published": True},
            {"type_id": 28430, "name": "Compressed Veldspar", "group_id": 462,
             "category_id": 25, "market_group_id": 1, "volume": 0.15,
             "portion_size": 1, "published": True},
        ])
        await session.commit()
    _use_fuzzwork({
        1230: FuzzworkAggregate(buy=_side("2.00"), sell=_side("2.00")),
        28430: FuzzworkAggregate(buy=_side("5.00"), sell=_side("5.00")),
    })
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        await http.put(
            "/api/v1/corporations/me/rules/market_group/1",
            json={"percentage": "100", "compressed_only": True},
        )
        resp = await http.post("/api/v1/appraisals", json={"items": [
            {"type_id": 1230, "quantity": 100},   # raw → rejected
            {"type_id": 28430, "quantity": 100},  # compressed → accepted
        ]})
        lines = {ln["type_name"]: ln for ln in resp.json()["lines"]}
        assert lines["Veldspar"]["status"] == "rejected"
        assert lines["Veldspar"]["reason"] == "Compressed only"
        assert lines["Compressed Veldspar"]["status"] == "accepted"
        assert Decimal(lines["Compressed Veldspar"]["line_total"]) == Decimal("500.00")


async def test_appraisal_ore_without_reprocess_rule_is_direct():
    await _seed_ore()
    _use_ore_fuzzwork()
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        # No reprocess rule → direct ore price at the default 90% buy: 100 × 2.00 × 90%.
        resp = await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 1230, "quantity": 100}]}
        )
        assert Decimal(resp.json()["lines"][0]["line_total"]) == Decimal("180.00")


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
        assert body["created_by_character_name"] == "Boss"  # name, resolved by join
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


async def test_appraisal_paste_ambiguous_name_is_rejected():
    # EVE SDE has duplicate type names; a pasted name that matches more than one
    # type must NOT silently resolve to an arbitrary one — it's rejected.
    async with SessionLocal() as session:
        await sde_repo.bulk_upsert_market_groups(
            session, [{"market_group_id": 1, "parent_id": None, "name": "Ore"}]
        )
        await sde_repo.bulk_upsert_types(
            session,
            [
                {"type_id": 34, "name": "Tritanium", "group_id": 18,
                 "market_group_id": 1, "volume": 0.01, "published": True},
                {"type_id": 35, "name": "Tritanium", "group_id": 18,
                 "market_group_id": 1, "volume": 0.01, "published": True},
            ],
        )
        await session.commit()
    _use_fuzzwork({34: FuzzworkAggregate(buy=_side("5.00"), sell=_side("8.00"))})
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        resp = await http.post(
            "/api/v1/appraisals", json={"paste": "Tritanium 1000"}
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["rejected_count"] == 1
        assert Decimal(body["accepted_total"]) == 0
        line = body["lines"][0]
        assert line["status"] == "rejected"
        assert line["type_id"] is None
        assert line["reason"] == "Ambiguous name (2 matches)"


JITA = "60003760"
AMARR = "60008494"


async def test_rule_hub_prices_line_at_override_hub():
    # Type rule sends Tritanium to Amarr; Pyerite stays on the Jita default
    # (ADR-0031). Each line prices from its own hub and the override is annotated.
    await _seed_sde()
    fake = _use_fuzzwork_by_station({
        JITA: {
            34: FuzzworkAggregate(buy=_side("5.00"), sell=_side("5.00")),
            35: FuzzworkAggregate(buy=_side("2.00"), sell=_side("2.00")),
        },
        AMARR: {34: FuzzworkAggregate(buy=_side("10.00"), sell=_side("10.00"))},
    })
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        await http.put(
            "/api/v1/corporations/me/rules/type/34",
            json={"percentage": "90", "market_hub_id": AMARR},
        )
        resp = await http.post(
            "/api/v1/appraisals",
            json={"items": [
                {"type_id": 34, "quantity": 10},
                {"type_id": 35, "quantity": 10},
            ]},
        )
        assert resp.status_code == 201
        lines = {ln["type_id"]: ln for ln in resp.json()["lines"]}

        # Tritanium priced at Amarr's 10.00 (90% → 9.00), annotated with the hub.
        assert Decimal(lines[34]["unit_price"]) == Decimal("9.00")
        assert lines[34]["market_hub_id"] == AMARR
        assert "Amarr" in lines[34]["market_hub_name"]

        # Pyerite priced at the Jita default (90% of 2.00), no annotation.
        assert Decimal(lines[35]["unit_price"]) == Decimal("1.80")
        assert lines[35]["market_hub_id"] is None

        assert sorted(set(fake.stations)) == [JITA, AMARR]


async def test_rule_hub_equal_to_config_hub_dedupes():
    # An override pointing at the corp's own hub is a no-op: one fetch, no annotation.
    await _seed_sde()
    fake = _use_fuzzwork_by_station(
        {JITA: {34: FuzzworkAggregate(buy=_side("5.00"), sell=_side("5.00"))}}
    )
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        await http.put(
            "/api/v1/corporations/me/rules/type/34",
            json={"percentage": "90", "market_hub_id": JITA},
        )
        resp = await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 34, "quantity": 10}]}
        )
        assert resp.status_code == 201
        line = resp.json()["lines"][0]
        assert line["status"] == "accepted"
        assert line["market_hub_id"] is None
        assert fake.stations == [JITA]


async def test_rule_hub_reprocess_prices_minerals_at_override_hub():
    # A reprocess rule with a hub values the ore's minerals (and leftover) there.
    await _seed_ore()
    fake = _use_fuzzwork_by_station({
        AMARR: {
            34: FuzzworkAggregate(buy=_side("100.00"), sell=_side("100.00")),
            1230: FuzzworkAggregate(buy=_side("2.00"), sell=_side("2.00")),
        },
    })
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        await http.put(
            "/api/v1/corporations/me/rules/type/1230",
            json={"percentage": "90", "reprocess": True, "market_hub_id": AMARR},
        )
        resp = await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 1230, "quantity": 100}]}
        )
        assert resp.status_code == 201
        line = resp.json()["lines"][0]
        # 1 batch × 400 Trit × 0.9063 × 100.00 = 36252 → unit 362.52 → 90% rounds
        # to 326.27/unit (ADR-0021 per-unit rounding) → ×100 = 32627.00
        assert line["status"] == "accepted"
        assert Decimal(line["line_total"]) == Decimal("32627.00")
        assert line["market_hub_id"] == AMARR
        # Everything (ore + minerals) was fetched from the override hub only.
        assert set(fake.stations) == {AMARR}


async def test_same_hub_under_drifted_descriptors_merges_price_maps():
    # Two rules can reference the same hub through descriptors that differ only in
    # the save-time-cached region_id (SDE drift between saves). They bucket
    # separately but their fetches must MERGE into one price map — an overwrite
    # would wrongly reject the first bucket's lines as "No market data".
    await _seed_sde()
    _use_fuzzwork_by_station({
        AMARR: {
            34: FuzzworkAggregate(buy=_side("10.00"), sell=_side("10.00")),
            35: FuzzworkAggregate(buy=_side("4.00"), sell=_side("4.00")),
        },
    })
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        # Write the drifted rows directly — the API resolves both identically.
        async with SessionLocal() as session:
            corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
            for tid, region in ((34, 10000043), (35, 99999999)):
                await rules_repo.upsert_rule(
                    session, corporation_id=corp.id, target_kind="type",
                    target_id=tid, basis=None, percentage=Decimal("90"),
                    enabled=True, reprocess=False, compressed_only=False,
                    accepted=True, market_hub_id=AMARR,
                    market_hub_kind="npc_station", market_region_id=region,
                    market_hub_name="Amarr",
                )
            await session.commit()

        resp = await http.post(
            "/api/v1/appraisals",
            json={"items": [
                {"type_id": 34, "quantity": 10},
                {"type_id": 35, "quantity": 10},
            ]},
        )
        assert resp.status_code == 201
        lines = {ln["type_id"]: ln for ln in resp.json()["lines"]}
        assert lines[34]["status"] == "accepted"
        assert lines[35]["status"] == "accepted"


async def test_rule_hub_without_data_rejects_only_those_lines():
    # A dead override hub rejects its lines; the default hub still prices the rest.
    await _seed_sde()
    _use_fuzzwork_by_station({
        JITA: {35: FuzzworkAggregate(buy=_side("2.00"), sell=_side("2.00"))},
        AMARR: {},
    })
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        await http.put(
            "/api/v1/corporations/me/rules/type/34",
            json={"percentage": "90", "market_hub_id": AMARR},
        )
        resp = await http.post(
            "/api/v1/appraisals",
            json={"items": [
                {"type_id": 34, "quantity": 10},
                {"type_id": 35, "quantity": 10},
            ]},
        )
        assert resp.status_code == 201
        lines = {ln["type_id"]: ln for ln in resp.json()["lines"]}
        assert lines[34]["status"] == "rejected"
        assert lines[34]["reason"] == "No market data"
        assert lines[34]["market_hub_id"] == AMARR  # annotated even on rejection
        assert lines[35]["status"] == "accepted"


async def test_appraisal_defaults_drop_off_to_market_hub():
    # No accepted locations configured → the appraisal records the market hub (ADR-0030).
    await _seed_sde()
    _use_fuzzwork({34: FuzzworkAggregate(buy=_side("5.00"), sell=_side("8.00"))})
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        resp = await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 34, "quantity": 1}]}
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["delivery_location_id"] == "60003760"  # default Jita hub
        assert body["delivery_location_name"]  # resolved to a friendly name


async def _add_structure_location(http, location_id: str, name: str) -> None:
    resp = await http.post(
        "/api/v1/corporations/me/locations",
        json={"location_id": location_id, "kind": "structure", "name": name},
    )
    assert resp.status_code == 201


async def test_appraisal_requires_drop_off_when_locations_configured():
    await _seed_sde()
    _use_fuzzwork({34: FuzzworkAggregate(buy=_side("5.00"), sell=_side("8.00"))})
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        await _add_structure_location(http, "1035000000001", "1DQ - Palace")

        # Omitting the drop-off is rejected now that a location exists.
        missing = await http.post(
            "/api/v1/appraisals", json={"items": [{"type_id": 34, "quantity": 1}]}
        )
        assert missing.status_code == 422

        # An id outside the accepted list is rejected.
        bad = await http.post(
            "/api/v1/appraisals",
            json={
                "items": [{"type_id": 34, "quantity": 1}],
                "delivery_location_id": "60003760",
            },
        )
        assert bad.status_code == 422

        # The accepted location is snapshotted onto the appraisal.
        ok = await http.post(
            "/api/v1/appraisals",
            json={
                "items": [{"type_id": 34, "quantity": 1}],
                "delivery_location_id": "1035000000001",
            },
        )
        assert ok.status_code == 201
        body = ok.json()
        assert body["delivery_location_id"] == "1035000000001"
        assert body["delivery_location_name"] == "1DQ - Palace"


async def test_appraisal_requires_items_or_paste():
    _use_fuzzwork({})  # so the fuzzwork dependency resolves; body validation fails first
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        resp = await http.post("/api/v1/appraisals", json={})
        assert resp.status_code == 422  # neither items nor paste


async def test_appraisal_accepts_exactly_1000_items():
    # 1000 = EVE's per-contract item limit — the boundary is allowed.
    _use_fuzzwork({})
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        paste = "\n".join(f"Unknown Item {i} 1" for i in range(1000))
        resp = await http.post("/api/v1/appraisals", json={"paste": paste})
        assert resp.status_code == 201
        assert resp.json()["rejected_count"] == 1000  # all unknown, but persisted


async def test_appraisal_rejects_more_than_1000_combined_items():
    # Structured + paste are counted together; 1001 total is over the contract limit.
    _use_fuzzwork({})
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        items = [{"type_id": 34, "quantity": 1} for _ in range(500)]
        paste = "\n".join(f"Unknown Item {i} 1" for i in range(501))
        resp = await http.post(
            "/api/v1/appraisals", json={"items": items, "paste": paste}
        )
        assert resp.status_code == 422
        assert "1000" in resp.json()["detail"]


async def test_appraisal_rejects_over_1000_structured_items_at_validation():
    # The structured list alone is bounded at the DTO (cheap first line of defense).
    _use_fuzzwork({})
    async with make_client(CeoEsi()) as http:
        await login(http)
        await http.post("/api/v1/corporations")
        items = [{"type_id": 34, "quantity": 1} for _ in range(1001)]
        resp = await http.post("/api/v1/appraisals", json={"items": items})
        assert resp.status_code == 422


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
            other_corp = await corporations_repo.create_corporation(
                session, eve_corporation_id=99999999, name="Other Corp",
                ceo_character_id=1, registered_by_character_id=1,
            )
            other = await appraisals_repo.create_appraisal(
                session,
                public_id="otherCorpXyz",
                corporation_id=other_corp.id,
                created_by_character_id=1,
                market_hub_id="60003760",
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
        corp = await corporations_repo.create_corporation(
            session, eve_corporation_id=CORP_ID, name="Test Corp",
            ceo_character_id=99999, registered_by_character_id=99999,
        )
        await config_repo.upsert_config(
            session, corporation_id=corp.id, market_hub_id="60003760",
            default_basis="buy", default_percentage=90, aggregate_field="percentile",
        )
        # An appraisal owned by a different character in the same corp.
        await appraisals_repo.create_appraisal(
            session, public_id="teammateAppr", corporation_id=corp.id,
            created_by_character_id=777, market_hub_id="60003760",
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
