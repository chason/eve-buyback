"""Pure rules for the lot ledger (ADR-0043). No I/O: use cases load lots and feed
plain values here; these functions decide cost, consumption order, value, and profit.
Money is Decimal (ADR-0020).

The core principles these encode:
- Inventory is carried at COST (landed: purchase + allocated hauling), never at
  hoped-for sale price.
- Lots are consumed FIFO per (type, location) — oldest first — so the audit trail
  stays clean and aging is trivial.
- Write-downs floor the carried cost when net realizable value falls below it, and
  are NEVER reversed upward (conservatism).
- Lot state is derived from allocations, not stored: one lot can be part idle, part
  listed, part contracted, part in transit.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

# How a lot came to exist (ADR-0043/0047): a completed buyback contract (exact cost),
# the opening-balance / off-app import (deemed cost), a manual entry, or the child of
# a reprocess transformation (cost inherited from the source lot).
LotSource = Literal["buyback", "opening_balance", "manual", "reprocess"]

# Where a sale happened (ADR-0045): a market fill, an in-game contract, or a direct
# (off-game negotiated) deal.
SaleChannel = Literal["market", "contract", "direct"]

# Provenance of a ledger entry (ADR-0045): detected via ESI, or entered by a manager.
# Orthogonal to `cost_is_estimated` (provenance vs cost confidence) — never conflate.
EntrySource = Literal["esi", "manual"]

# Costs not embedded in a lot's basis (ADR-0043/0045): selling fees, outbound freight
# (hauling is a SELLING cost in this app — members haul in, ADR-0030), write-downs,
# and anything else a manager needs to book.
ExpenseKind = Literal["broker_fee", "relist_fee", "hauling", "write_down", "other"]


def landed_unit_cost(
    unit_purchase_cost: Decimal,
    unit_hauling_cost: Decimal,
    written_down_to: Decimal | None,
) -> Decimal:
    """What one unit is carried at: purchase + allocated hauling, floored to the
    written-down value when a write-down was taken (and never raised by one)."""
    base = unit_purchase_cost + unit_hauling_cost
    if written_down_to is not None and written_down_to < base:
        return written_down_to
    return base


def qty_idle(
    qty_remaining: int, *, on_orders: int = 0, on_contracts: int = 0, in_transit: int = 0
) -> int:
    """Units physically sitting in the hangar: what remains minus what has left it
    (market escrow, contract escrow, a freighter). Lot state is DERIVED — this is the
    quantity the hangar reconciliation (ADR-0044) compares against. Can go negative
    when the books are inconsistent; callers surface that, not clamp it."""
    return qty_remaining - on_orders - on_contracts - in_transit


@dataclass(frozen=True)
class OpenLot:
    """One open lot as the FIFO planner sees it."""

    lot_id: uuid.UUID
    qty_remaining: int
    acquired_at: datetime


@dataclass(frozen=True)
class LotConsumption:
    """Take `qty` units from this lot."""

    lot_id: uuid.UUID
    qty: int


@dataclass(frozen=True)
class FifoPlan:
    """The consumption plan for a quantity: which lots give how much, oldest first.
    `shortfall` > 0 means the open lots couldn't cover it — the caller decides
    (deemed COGS or a reconciliation exception, ADR-0045); nothing is invented here."""

    consumptions: tuple[LotConsumption, ...]
    shortfall: int


def plan_fifo(lots: list[OpenLot], qty: int) -> FifoPlan:
    """Plan consuming `qty` units FIFO: oldest `acquired_at` first (lot id as a
    deterministic tiebreak). Pure — the caller applies the plan to the ledger."""
    remaining = qty
    consumptions: list[LotConsumption] = []
    for lot in sorted(lots, key=lambda x: (x.acquired_at, str(x.lot_id))):
        if remaining <= 0:
            break
        if lot.qty_remaining <= 0:
            continue
        take = min(lot.qty_remaining, remaining)
        consumptions.append(LotConsumption(lot_id=lot.lot_id, qty=take))
        remaining -= take
    return FifoPlan(consumptions=tuple(consumptions), shortfall=remaining)


def nrv_per_unit(
    expected_price: Decimal,
    *,
    sales_tax_rate: Decimal,
    outbound_cost_per_unit: Decimal = Decimal(0),
) -> Decimal:
    """Net realizable value: what one unit would actually net if sold now — the
    expected price less sales tax and the estimated cost of getting it to market."""
    return expected_price * (Decimal(1) - sales_tax_rate) - outbound_cost_per_unit


def write_down_target(landed_cost: Decimal, nrv: Decimal) -> Decimal | None:
    """The per-unit value to write the lot down to, or None when no write-down is due.
    Only ever downward: NRV above cost never raises the carried value (conservatism —
    the paper gain stays unrealized until a real sale)."""
    return nrv if nrv < landed_cost else None


def realized_profit(
    *,
    qty: int,
    unit_proceeds: Decimal,
    sales_tax: Decimal,
    landed_unit_cost: Decimal,
    fees: Decimal = Decimal(0),
) -> Decimal:
    """Realized profit for one sale event against one lot (ADR-0043):
    proceeds − tax − cost of the goods sold − attributed selling fees."""
    return qty * unit_proceeds - sales_tax - qty * landed_unit_cost - fees
