from decimal import Decimal

from app.domain.ids import generate_appraisal_id
from app.domain.pricing import (
    RuleSpec,
    line_total,
    resolve_rule,
    round_isk,
    select_aggregate,
    unit_price,
)

DEF = {"default_basis": "buy", "default_percentage": Decimal("90")}


def test_type_rule_wins_over_group():
    r = resolve_rule(
        34, 100,
        type_rules={34: RuleSpec("sell", Decimal("80"))},
        group_rules={100: RuleSpec("buy", Decimal("50"))},
        parent_of={100: None}, **DEF,
    )
    assert (r.basis, r.percentage, r.source) == ("sell", Decimal("80"), "type:34")


def test_nearest_ancestor_group_wins():
    # type's group 3 → 2 → 1; rules on 2 and 1; nearest (2) wins.
    r = resolve_rule(
        34, 3,
        type_rules={},
        group_rules={2: RuleSpec("buy", Decimal("60")), 1: RuleSpec("buy", Decimal("70"))},
        parent_of={3: 2, 2: 1, 1: None}, **DEF,
    )
    assert r.source == "market_group:2" and r.percentage == Decimal("60")


def test_walks_up_to_ancestor_rule():
    r = resolve_rule(
        34, 3,
        type_rules={},
        group_rules={1: RuleSpec("buy", Decimal("70"))},
        parent_of={3: 2, 2: 1, 1: None}, **DEF,
    )
    assert r.source == "market_group:1"


def test_default_fallback():
    r = resolve_rule(
        34, 3, type_rules={}, group_rules={}, parent_of={3: 2, 2: 1, 1: None}, **DEF
    )
    assert (r.basis, r.percentage, r.source) == ("buy", Decimal("90"), "default")


def test_none_market_group_falls_through_to_default():
    r = resolve_rule(34, None, type_rules={}, group_rules={}, parent_of={}, **DEF)
    assert r.source == "default"


def test_group_rule_basis_none_inherits_default_basis():
    r = resolve_rule(
        34, 1,
        type_rules={},
        group_rules={1: RuleSpec(None, Decimal("75"))},
        parent_of={1: None},
        default_basis="sell", default_percentage=Decimal("90"),
    )
    assert r.basis == "sell" and r.percentage == Decimal("75")


def test_select_aggregate_sides():
    assert select_aggregate(Decimal("5"), Decimal("8"), "buy") == Decimal("5")
    assert select_aggregate(Decimal("5"), Decimal("8"), "sell") == Decimal("8")
    assert select_aggregate(Decimal("5"), Decimal("8"), "split") == Decimal("6.5")


def test_select_aggregate_missing_side_is_none():
    assert select_aggregate(None, Decimal("8"), "buy") is None
    assert select_aggregate(Decimal("5"), None, "split") is None
    assert select_aggregate(None, None, "split") is None


def test_round_isk_is_half_even():
    assert round_isk(Decimal("0.125")) == Decimal("0.12")  # 2 is even, stays
    assert round_isk(Decimal("0.135")) == Decimal("0.14")  # 3 is odd, rounds up
    assert round_isk(Decimal("1.005")) == Decimal("1.00")  # 0 is even, stays


def test_unit_price_and_line_total():
    up = unit_price(Decimal("5.00"), Decimal("90"))  # 5 * 90% = 4.50
    assert up == Decimal("4.50")
    assert line_total(up, 1000) == Decimal("4500.00")


def test_generate_appraisal_id_shape_and_uniqueness():
    a = generate_appraisal_id()
    assert len(a) == 12
    assert a != generate_appraisal_id()
