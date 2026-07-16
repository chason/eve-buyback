"""#177 / ADR-0047: reprocess transformations — value-weighted cost allocation that
sums exactly, base-yield prefill, inheritance into child lots, source-agnostic
recording (a module lot), the hint matcher, and the API round trip."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.application import transformations as transformations_app
from app.application.errors import LotNotFound, ReprocessQtyUnavailable
from app.data.db import SessionLocal
from app.data.models import MarketPrice
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import sde as sde_repo
from app.domain.transformations import (
    OutputLine,
    allocate_source_cost,
    base_yield_outputs,
    match_reprocess_hints,
)
from app.main import app
from tests.helpers import CHAR_ID, CORP_ID, CeoEsi, MemberEsi, login, make_client

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
JITA = "60003760"
VELD = 1230  # Veldspar: portion 100 → 400 Tritanium per batch (mini-SDE convention)
TRIT = 34
PYE = 35
MEX = 36


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# --- allocation math -------------------------------------------------------------


def test_allocation_is_value_weighted_and_sums_exactly():
    # 1000 ISK across Trit (400 units @4 = 1600 value) and Mex (8 units @50 = 400):
    # 80% / 20% by value, NOT by quantity.
    allocated = allocate_source_cost(
        Decimal("1000.00"),
        [
            OutputLine(type_id=TRIT, quantity=400, unit_value=Decimal("4.00")),
            OutputLine(type_id=MEX, quantity=8, unit_value=Decimal("50.00")),
        ],
    )
    totals = {a.type_id: a.total_cost for a in allocated}
    assert totals == {TRIT: Decimal("800.00"), MEX: Decimal("200.00")}
    assert sum(a.total_cost for a in allocated) == Decimal("1000.00")
    trit = next(a for a in allocated if a.type_id == TRIT)
    assert trit.unit_cost == Decimal("2.00")  # 800 / 400


def test_allocation_hands_out_remainder_cents_exactly():
    # 100 ISK across three equal-value outputs: 33.33 + 33.33 + 33.34 — never 99.99.
    allocated = allocate_source_cost(
        Decimal("100.00"),
        [
            OutputLine(type_id=1, quantity=1, unit_value=Decimal(1)),
            OutputLine(type_id=2, quantity=1, unit_value=Decimal(1)),
            OutputLine(type_id=3, quantity=1, unit_value=Decimal(1)),
        ],
    )
    assert sum(a.total_cost for a in allocated) == Decimal("100.00")
    assert sorted(a.total_cost for a in allocated) == [
        Decimal("33.33"), Decimal("33.33"), Decimal("33.34"),
    ]


def test_allocation_no_write_up_when_outputs_worth_more():
    # 1M ISK of input becomes materials "worth" 1.3M — the children still carry 1M.
    allocated = allocate_source_cost(
        Decimal("1000000.00"),
        [OutputLine(type_id=TRIT, quantity=325_000, unit_value=Decimal("4.00"))],
    )
    assert allocated[0].total_cost == Decimal("1000000.00")


def test_allocation_unpriced_outputs_carry_no_weight_unless_nothing_is_priced():
    # A priced and an unpriced output: all cost flows to the priced one.
    allocated = allocate_source_cost(
        Decimal("100.00"),
        [
            OutputLine(type_id=TRIT, quantity=10, unit_value=Decimal("4.00")),
            OutputLine(type_id=PYE, quantity=10, unit_value=None),
        ],
    )
    totals = {a.type_id: a.total_cost for a in allocated}
    assert totals == {TRIT: Decimal("100.00"), PYE: Decimal("0.00")}

    # NOTHING priced → per-quantity fallback so the cost still flows somewhere.
    allocated = allocate_source_cost(
        Decimal("100.00"),
        [
            OutputLine(type_id=TRIT, quantity=75, unit_value=None),
            OutputLine(type_id=PYE, quantity=25, unit_value=None),
        ],
    )
    totals = {a.type_id: a.total_cost for a in allocated}
    assert totals == {TRIT: Decimal("75.00"), PYE: Decimal("25.00")}


# --- base-yield prefill ------------------------------------------------------------


def test_base_yield_prefill_floors_batches_and_units():
    # 250 units, portion 100 → 2 whole batches; 400 Trit/batch × 0.9063 → 725.04 → 725.
    outputs = base_yield_outputs(250, 100, [(TRIT, 400), (PYE, 1)])
    assert outputs == {TRIT: 725, PYE: 1}  # 2 × 1 × 0.9063 = 1.81 → 1


def test_base_yield_prefill_empty_below_one_batch_or_without_data():
    assert base_yield_outputs(99, 100, [(TRIT, 400)]) == {}
    assert base_yield_outputs(500, 100, []) == {}


# --- hint matching -----------------------------------------------------------------


def test_hint_matches_yield_consistent_pattern_only():
    materials = {VELD: [(TRIT, 400), (PYE, 1)]}
    portions = {VELD: 100}
    # 600 Veldspar short (6 batches) alongside ≤ 2400 Trit excess → looks reprocessed.
    hints = match_reprocess_hints(
        [(JITA, VELD, 600)], [(JITA, TRIT, 2200)], materials, portions
    )
    assert len(hints) == 1
    assert hints[0].type_id == VELD and hints[0].material_type_ids == {TRIT}

    # More Trit than 6 batches could EVER yield → not explained by this reprocess.
    assert (
        match_reprocess_hints(
            [(JITA, VELD, 600)], [(JITA, TRIT, 2500)], materials, portions
        )
        == []
    )
    # Excess of something Veldspar doesn't yield → no match.
    assert (
        match_reprocess_hints(
            [(JITA, VELD, 600)], [(JITA, MEX, 10)], materials, portions
        )
        == []
    )
    # A type with no yield data can't hint.
    assert (
        match_reprocess_hints(
            [(JITA, TRIT, 600)], [(JITA, PYE, 10)], materials, portions
        )
        == []
    )


# --- recording (application) --------------------------------------------------------


async def _seed(*, prices: dict[int, str] | None = None):
    async with SessionLocal() as session:
        corp = await corporations_repo.create_corporation(
            session, eve_corporation_id=CORP_ID, name="Test Corp",
            ceo_character_id=CHAR_ID, registered_by_character_id=CHAR_ID,
        )
        await entitlements_repo.upsert(
            session, corporation_id=corp.id, feature="accounting",
            source="admin", expires_at=None,
        )
        await config_repo.upsert_config(
            session, corporation_id=corp.id, market_hub_id=JITA,
            default_basis="buy", default_percentage=90,
            aggregate_field="percentile",
        )
        await sde_repo.bulk_upsert_types(session, [
            {"type_id": VELD, "name": "Veldspar", "group_id": 462,
             "market_group_id": 1, "volume": 0.1, "portion_size": 100,
             "published": True},
            {"type_id": TRIT, "name": "Tritanium", "group_id": 18,
             "market_group_id": 1857, "volume": 0.01, "portion_size": 1,
             "published": True},
            {"type_id": PYE, "name": "Pyerite", "group_id": 18,
             "market_group_id": 1857, "volume": 0.01, "portion_size": 1,
             "published": True},
        ])
        await sde_repo.bulk_upsert_type_materials(session, [
            {"type_id": VELD, "material_type_id": TRIT, "quantity": 400},
            {"type_id": VELD, "material_type_id": PYE, "quantity": 1},
        ])
        for type_id, buy in (prices or {}).items():
            b = Decimal(buy)
            session.add(MarketPrice(
                hub_id=JITA, type_id=type_id,
                buy_weighted_average=b, buy_max=b, buy_min=b, buy_median=b,
                buy_percentile=b, buy_volume=Decimal(1000), buy_order_count=10,
                sell_weighted_average=b, sell_max=b, sell_min=b, sell_median=b,
                sell_percentile=b, sell_volume=Decimal(1000), sell_order_count=10,
                fetched_at=NOW,
            ))
        await session.commit()
        return corp.id


async def _lot(corp_id, *, type_id=VELD, qty=1000, cost="10.00", **kwargs):
    async with SessionLocal() as session:
        lot = await lots_repo.create_lot(
            session, corporation_id=corp_id, item_type_id=type_id, qty=qty,
            unit_purchase_cost=Decimal(cost),
            acquired_at=kwargs.pop("acquired_at", NOW - timedelta(days=20)),
            source=kwargs.pop("source", "buyback"),
            location_id=JITA, **kwargs,
        )
        await session.commit()
        return lot


async def test_record_reprocess_conserves_cost_and_inherits():
    corp_id = await _seed(prices={TRIT: "4.00", PYE: "8.00"})
    source = await _lot(corp_id, qty=1000, cost="10.00",
                        source="opening_balance", cost_is_estimated=True)

    async with SessionLocal() as session:
        children = await transformations_app.record_reprocess(
            session, corporation_eve_id=CORP_ID, lot_id=source.id,
            qty=1000, outputs={TRIT: 3600, PYE: 9}, now=NOW,
        )

    # Consumed cost 10,000: Trit value 14,400 vs Pye 72 → value-weighted split.
    total = sum(c.qty_original * c.unit_purchase_cost for c in children)
    assert total.quantize(Decimal("0.01")) == Decimal("10000.00")
    by_type = {c.item_type_id: c for c in children}
    assert by_type[TRIT].qty_original == 3600
    # Inheritance: same capital — same age, same cost confidence, same place.
    for child in children:
        assert child.source == "reprocess"
        assert child.source_lot_id == source.id
        assert child.acquired_at == source.acquired_at
        assert child.cost_is_estimated is True
        assert child.location_id == JITA
    # The source is spent.
    async with SessionLocal() as session:
        remaining = await lots_repo.open_lots(session, corporation_id=corp_id)
    assert {lot.item_type_id for lot in remaining} == {TRIT, PYE}


async def test_record_reprocess_works_on_any_source_type():
    corp_id = await _seed(prices={TRIT: "4.00"})
    # A module lot (no SDE row seeded at all) — source-agnostic by design.
    module = await _lot(corp_id, type_id=2046, qty=10, cost="1000.00")

    async with SessionLocal() as session:
        children = await transformations_app.record_reprocess(
            session, corporation_eve_id=CORP_ID, lot_id=module.id,
            qty=10, outputs={TRIT: 2000}, now=NOW,
        )

    assert children[0].unit_purchase_cost == Decimal("5.00")  # 10,000 / 2,000
    async with SessionLocal() as session:
        remaining = await lots_repo.open_lots(session, corporation_id=corp_id)
    assert {lot.item_type_id for lot in remaining} == {TRIT}


async def test_record_reprocess_guards_quantity_and_scope():
    corp_id = await _seed()
    source = await _lot(corp_id, qty=100)
    async with SessionLocal() as session:
        with pytest.raises(ReprocessQtyUnavailable):
            await transformations_app.record_reprocess(
                session, corporation_eve_id=CORP_ID, lot_id=source.id,
                qty=101, outputs={TRIT: 1},
            )
    import uuid

    async with SessionLocal() as session:
        with pytest.raises(LotNotFound):
            await transformations_app.record_reprocess(
                session, corporation_eve_id=CORP_ID, lot_id=uuid.uuid4(),
                qty=1, outputs={TRIT: 1},
            )


async def test_partial_reprocess_leaves_the_rest_of_the_lot():
    corp_id = await _seed(prices={TRIT: "4.00"})
    source = await _lot(corp_id, qty=1000, cost="10.00")

    async with SessionLocal() as session:
        await transformations_app.record_reprocess(
            session, corporation_eve_id=CORP_ID, lot_id=source.id,
            qty=400, outputs={TRIT: 1450}, now=NOW,
        )

    async with SessionLocal() as session:
        remaining = await lots_repo.open_lots(session, corporation_id=corp_id)
    by_type = {lot.item_type_id: lot for lot in remaining}
    assert by_type[VELD].qty_remaining == 600  # untouched remainder
    # The 400 consumed carried 4,000 ISK into the Tritanium child.
    trit = by_type[TRIT]
    assert (trit.qty_original * trit.unit_purchase_cost).quantize(
        Decimal("0.01")
    ) == Decimal("4000.00")


# --- API ----------------------------------------------------------------------------


async def test_preview_prefills_base_yields_via_api():
    corp_id = await _seed()
    source = await _lot(corp_id, qty=1000, cost="10.00")
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.get(
            f"/api/v1/corporations/me/accounting/lots/{source.id}/reprocess-preview"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type_name"] == "Veldspar"
    assert body["qty_remaining"] == 1000
    # 10 batches: 4000 Trit × 0.9063 → 3625; 10 Pye × 0.9063 → 9.
    assert {(o["type_id"], o["quantity"]) for o in body["outputs"]} == {
        (TRIT, 3625), (PYE, 9),
    }


async def test_record_via_api_and_member_is_403():
    corp_id = await _seed(prices={TRIT: "4.00"})
    source = await _lot(corp_id, qty=1000, cost="10.00")
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.post(
            f"/api/v1/corporations/me/accounting/lots/{source.id}/reprocess",
            json={"qty": 1000, "outputs": [{"type_id": TRIT, "quantity": 3625}]},
        )
    assert resp.status_code == 200
    assert resp.json()["children"] == [{
        "type_id": TRIT, "type_name": None, "quantity": 3625,
    }]

    app.dependency_overrides.clear()
    async with make_client(MemberEsi()) as http:
        await login(http)
        resp = await http.get(
            f"/api/v1/corporations/me/accounting/lots/{source.id}/reprocess-preview"
        )
    assert resp.status_code == 403
