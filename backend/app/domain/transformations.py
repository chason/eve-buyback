"""Pure rules for lot transformations (ADR-0047). No I/O: the use case loads the
source lot, the outputs the manager actually received, and their market values, and
feeds them here; these functions decide how the consumed cost flows into the child
lots and when a hangar difference looks like a reprocess.

Core principle — COST CONSERVATION, NO WRITE-UP: the children's combined basis
equals exactly the source cost consumed. If 1M ISK of ore becomes minerals "worth"
1.3M, the ledger still carries 1M until they actually sell (ADR-0043 conservatism
survives transformation).
"""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

# The app's assumed reprocess yield (ADR-0026) — used to PRE-FILL output quantities
# for the manual record action. Actual yields vary with skills and structures, which
# is exactly why the quantities stay editable and nothing auto-applies.
from app.domain.pricing import ORE_REFINE_YIELD as BASE_YIELD  # noqa: F401  (re-export)

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class OutputLine:
    """One material the reprocess actually produced, with its market unit value at
    split-off (None = no cached price)."""

    type_id: int
    quantity: int
    unit_value: Decimal | None


@dataclass(frozen=True)
class AllocatedOutput:
    """One child lot to create: `total_cost` is this output's exact share of the
    consumed source cost; `unit_cost` is `total_cost / quantity` (unquantized
    Decimal — Postgres Numeric keeps it; recomputed value matches to well below
    any display precision)."""

    type_id: int
    quantity: int
    unit_cost: Decimal
    total_cost: Decimal


def allocate_source_cost(
    consumed_cost: Decimal, outputs: list[OutputLine]
) -> list[AllocatedOutput]:
    """Split the consumed source cost across the outputs **pro-rata by market value
    at split-off** (the standard joint-products answer; per-quantity allocation
    would misprice scarce materials — a unit of Megacyte and a unit of Tritanium
    are not the same capital).

    Exactness: shares are floored to the ISK cent and the remaining cents are
    handed out by largest fractional remainder, so `Σ total_cost == consumed_cost`
    to the cent — nothing created, nothing destroyed. Unpriced outputs carry zero
    weight (no evidence to give them basis); if NOTHING is priced, allocation falls
    back to per-quantity so the cost still flows somewhere honest."""
    if not outputs:
        return []
    weights = [
        (line.unit_value or Decimal(0)) * line.quantity for line in outputs
    ]
    total_weight = sum(weights)
    if total_weight == 0:
        weights = [Decimal(line.quantity) for line in outputs]
        total_weight = sum(weights)

    # Largest-remainder at the cent: floor every share, then hand the leftover
    # cents to the largest fractional remainders (index as a deterministic tiebreak).
    ideal = [consumed_cost * w / total_weight for w in weights]
    floored = [share.quantize(_CENT, rounding=ROUND_FLOOR) for share in ideal]
    leftover_cents = int(
        ((consumed_cost - sum(floored)) / _CENT).to_integral_value()
    )
    by_remainder = sorted(
        range(len(outputs)), key=lambda i: (floored[i] - ideal[i], i)
    )
    totals = list(floored)
    for i in by_remainder[:leftover_cents]:
        totals[i] += _CENT

    return [
        AllocatedOutput(
            type_id=line.type_id,
            quantity=line.quantity,
            unit_cost=totals[i] / line.quantity,
            total_cost=totals[i],
        )
        for i, line in enumerate(outputs)
    ]


def base_yield_outputs(
    qty: int, portion_size: int, materials: list[tuple[int, int]]
) -> dict[int, int]:
    """The PRE-FILL quantities for a manual reprocess (ADR-0047): whole refine
    batches × per-portion material quantity × the app's assumed yield (ADR-0026),
    floored per material. Empty when the quantity doesn't cover one batch or the
    type has no yield data."""
    batches = qty // portion_size if portion_size > 0 else 0
    if batches <= 0:
        return {}
    out: dict[int, int] = {}
    for material_type_id, per_portion in materials:
        yielded = int(batches * per_portion * BASE_YIELD)
        if yielded > 0:
            out[material_type_id] = yielded
    return out


@dataclass(frozen=True)
class ReprocessHint:
    """A hangar difference that looks like an unrecorded reprocess (ADR-0047): a
    reprocessable type is short while its OWN materials are in excess at the same
    location. Surfaced as a suggestion — never auto-applied (actual yields vary
    with skills/structures, so a human confirms the real quantities)."""

    location_id: str
    type_id: int  # the reprocessable source type that is short
    qty: int  # how many units are missing
    material_type_ids: frozenset[int]  # the excess types the pattern explains


def match_reprocess_hints(
    shortfalls: list[tuple[str, int, int]],
    excesses: list[tuple[str, int, int]],
    materials_by_type: dict[int, list[tuple[int, int]]],
    portion_size_by_type: dict[int, int],
) -> list[ReprocessHint]:
    """Pattern-match shortfalls against excesses (both `(location, type, qty)`):
    a shortfall of a type with yield data, alongside excess **consisting of that
    type's materials** at the same location, in quantities the missing units could
    actually have produced (≤ whole batches × per-portion quantity at 100%
    recovery — real yields are lower, so anything above that bound can't be
    explained by this reprocess alone).

    Each excess slot backs at most one hint (first shortfall in deterministic
    order claims it), and a hint needs at least one matched material."""
    excess_by_slot = {(loc, tid): qty for loc, tid, qty in excesses}
    claimed: set[tuple[str, int]] = set()
    hints: list[ReprocessHint] = []
    for location_id, type_id, qty in sorted(shortfalls):
        materials = materials_by_type.get(type_id)
        if not materials:
            continue
        batches = qty // portion_size_by_type.get(type_id, 1)
        if batches <= 0:
            continue
        matched: set[int] = set()
        for material_type_id, per_portion in materials:
            slot = (location_id, material_type_id)
            if slot in claimed or slot not in excess_by_slot:
                continue
            if excess_by_slot[slot] > batches * per_portion:
                continue
            matched.add(material_type_id)
        if matched:
            for material_type_id in matched:
                claimed.add((location_id, material_type_id))
            hints.append(
                ReprocessHint(
                    location_id=location_id,
                    type_id=type_id,
                    qty=qty,
                    material_type_ids=frozenset(matched),
                )
            )
    return hints
