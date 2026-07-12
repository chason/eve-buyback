"""ADR-0043: the pure lot-ledger rules — landed cost, FIFO consumption, derived
idle state, NRV, write-down conservatism, and realized-profit math."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.lots import (
    FifoPlan,
    LotConsumption,
    OpenLot,
    landed_unit_cost,
    nrv_per_unit,
    plan_fifo,
    qty_idle,
    realized_profit,
    write_down_target,
)

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


def _lot(qty: int, days_ago: int, lot_id: uuid.UUID | None = None) -> OpenLot:
    return OpenLot(
        lot_id=lot_id or uuid.uuid4(),
        qty_remaining=qty,
        acquired_at=NOW - timedelta(days=days_ago),
    )


# --- landed cost -----------------------------------------------------------------


def test_landed_cost_is_purchase_plus_hauling():
    assert landed_unit_cost(Decimal("4.00"), Decimal("0.50"), None) == Decimal("4.50")


def test_landed_cost_floors_to_write_down():
    assert landed_unit_cost(Decimal("4.00"), Decimal("0.50"), Decimal("3.10")) == Decimal(
        "3.10"
    )


def test_write_down_never_raises_cost():
    # A "write-down" above the base cost is ignored — cost only moves down.
    assert landed_unit_cost(Decimal("4.00"), Decimal(0), Decimal("9.99")) == Decimal(
        "4.00"
    )


# --- FIFO consumption ------------------------------------------------------------


def test_fifo_consumes_oldest_first_and_splits_across_lots():
    old = _lot(100, days_ago=30)
    mid = _lot(50, days_ago=10)
    new = _lot(200, days_ago=1)
    # Deliberately passed out of order — the planner sorts by acquired_at.
    plan = plan_fifo([new, old, mid], 120)
    assert plan == FifoPlan(
        consumptions=(
            LotConsumption(lot_id=old.lot_id, qty=100),
            LotConsumption(lot_id=mid.lot_id, qty=20),
        ),
        shortfall=0,
    )


def test_fifo_reports_shortfall_instead_of_inventing_inventory():
    plan = plan_fifo([_lot(30, days_ago=5)], 100)
    assert plan.shortfall == 70
    assert sum(c.qty for c in plan.consumptions) == 30


def test_fifo_skips_empty_lots_and_ties_break_deterministically():
    a = OpenLot(
        lot_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        qty_remaining=10,
        acquired_at=NOW,
    )
    b = OpenLot(
        lot_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        qty_remaining=10,
        acquired_at=NOW,  # same instant — id breaks the tie
    )
    empty = _lot(0, days_ago=99)
    plan = plan_fifo([b, empty, a], 15)
    assert [c.lot_id for c in plan.consumptions] == [a.lot_id, b.lot_id]
    assert [c.qty for c in plan.consumptions] == [10, 5]


def test_fifo_zero_quantity_is_a_noop():
    assert plan_fifo([_lot(10, days_ago=1)], 0) == FifoPlan(consumptions=(), shortfall=0)


# --- derived idle state ----------------------------------------------------------


def test_qty_idle_subtracts_everything_that_left_the_hangar():
    assert qty_idle(100, on_orders=30, on_contracts=20, in_transit=10) == 40


def test_qty_idle_can_go_negative_for_reconciliation_to_flag():
    # Inconsistent books surface as a negative — flagged, not clamped away.
    assert qty_idle(10, on_orders=25) == -15


# --- NRV + write-down ------------------------------------------------------------


def test_nrv_nets_out_tax_and_outbound_costs():
    nrv = nrv_per_unit(
        Decimal("100"),
        sales_tax_rate=Decimal("0.036"),
        outbound_cost_per_unit=Decimal("1.40"),
    )
    assert nrv == Decimal("95.00")


def test_write_down_only_when_nrv_below_cost():
    assert write_down_target(Decimal("4.00"), Decimal("3.10")) == Decimal("3.10")
    assert write_down_target(Decimal("4.00"), Decimal("4.00")) is None
    assert write_down_target(Decimal("4.00"), Decimal("9.00")) is None  # never up


# --- realized profit ---------------------------------------------------------------


def test_realized_profit_math():
    # 100 units sold at 5.00, 18.00 tax, carried at 3.60, 25.00 fees:
    # 500 − 18 − 360 − 25 = 97 — exact Decimal, no drift.
    profit = realized_profit(
        qty=100,
        unit_proceeds=Decimal("5.00"),
        sales_tax=Decimal("18.00"),
        landed_unit_cost=Decimal("3.60"),
        fees=Decimal("25.00"),
    )
    assert profit == Decimal("97.00")


def test_realized_loss_is_negative():
    profit = realized_profit(
        qty=10,
        unit_proceeds=Decimal("1.00"),
        sales_tax=Decimal(0),
        landed_unit_cost=Decimal("2.00"),
    )
    assert profit == Decimal("-10.00")
