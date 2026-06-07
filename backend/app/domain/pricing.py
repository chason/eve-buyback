"""Domain rules for buyback pricing (ADR-0007 resolution, ADR-0020 money, ADR-0021
computation). Small, pure functions with no I/O — and the canonical `Literal`s that
the DB CHECK columns and the API DTOs both derive from. The application layer pulls
plain `Decimal`s out of the data records and passes them here; this module never
imports `data/`."""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

Basis = Literal["buy", "sell", "split"]
AggregateField = Literal[
    "weighted_average", "max", "min", "median", "percentile"
]
TargetKind = Literal["market_group", "type"]
LineStatus = Literal["accepted", "rejected"]

# The default "global" rule for a freshly registered corp (ADR-0007): 90% Jita Buy
# on the manipulation-resistant percentile aggregate (ADR-0006). The hub itself comes
# from settings at creation time.
DEFAULT_BASIS: Basis = "buy"
DEFAULT_PERCENTAGE = Decimal("90")
DEFAULT_AGGREGATE_FIELD: AggregateField = "percentile"

# Ore reprocess pricing (ADR-0026). Ores are SDE category 25 (Asteroid). A "perfect"
# ore refine yields 0.9063 of the base material quantities (the max achievable; not
# 100%). Gas/scrap have other yields but we only price ores.
ORE_CATEGORY_ID = 25
ORE_REFINE_YIELD = Decimal("0.9063")
# Compressed ore type names start with this prefix (e.g. "Compressed Veldspar").
_COMPRESSED_PREFIX = "Compressed "


def is_compressed_ore(name: str) -> bool:
    """Whether an ore type name is the compressed variant (ADR-0026)."""
    return name.startswith(_COMPRESSED_PREFIX)

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class RuleSpec:
    """A pricing rule reduced to what resolution needs. `basis is None` means
    "inherit the config default basis"."""

    basis: Basis | None
    percentage: Decimal
    reprocess: bool = False
    compressed_only: bool = False
    accepted: bool = True


@dataclass(frozen=True)
class ResolvedRule:
    basis: Basis
    percentage: Decimal
    source: str  # which rule won: "type:34", "market_group:1857", or "default"
    reprocess: bool = False  # price a matched ore by its refined minerals (ADR-0026)
    compressed_only: bool = False  # reject the uncompressed variants of matched ores
    accepted: bool = True  # False → the buyback rejects matching items (blacklist)


def resolve_rule(
    type_id: int,
    market_group_id: int | None,
    *,
    type_rules: dict[int, RuleSpec],
    group_rules: dict[int, RuleSpec],
    parent_of: dict[int, int | None],
    default_basis: Basis,
    default_percentage: Decimal,
) -> ResolvedRule:
    """Most-specific-wins resolution for one type (ADR-0007):

    1. an exact `type` rule, else
    2. the nearest enabled ancestor `market_group` rule (walk `parent_of` up from
       the type's `market_group_id`), else
    3. the corp's global default.

    A matched rule with `basis is None` inherits `default_basis`.
    """
    type_rule = type_rules.get(type_id)
    if type_rule is not None:
        return ResolvedRule(
            basis=type_rule.basis or default_basis,
            percentage=type_rule.percentage,
            source=f"type:{type_id}",
            reprocess=type_rule.reprocess,
            compressed_only=type_rule.compressed_only,
            accepted=type_rule.accepted,
        )

    group_id = market_group_id
    seen: set[int] = set()
    while group_id is not None and group_id not in seen:
        seen.add(group_id)
        group_rule = group_rules.get(group_id)
        if group_rule is not None:
            return ResolvedRule(
                basis=group_rule.basis or default_basis,
                percentage=group_rule.percentage,
                source=f"market_group:{group_id}",
                reprocess=group_rule.reprocess,
                compressed_only=group_rule.compressed_only,
                accepted=group_rule.accepted,
            )
        group_id = parent_of.get(group_id)

    return ResolvedRule(
        basis=default_basis, percentage=default_percentage, source="default"
    )


def select_aggregate(
    buy: Decimal | None, sell: Decimal | None, basis: Basis
) -> Decimal | None:
    """Pick the market unit value for a basis. Returns None when a needed side is
    unavailable (the caller passes None for a side with no orders) — the line is
    then rejected upstream (ADR-0008 minimal data-quality)."""
    if basis == "buy":
        return buy
    if basis == "sell":
        return sell
    if buy is None or sell is None:
        return None
    return (buy + sell) / Decimal(2)


@dataclass(frozen=True)
class ReprocessMineral:
    """One mineral a reprocessed ore line yields (across all its refine batches)."""

    type_id: int
    quantity: Decimal  # yielded amount = batches × base_qty × yield
    unit_value: Decimal | None  # the mineral's market unit value (None if unpriced)
    value: Decimal  # quantity × unit_value (market; 0 if unpriced)


@dataclass(frozen=True)
class ReprocessResult:
    total: Decimal  # total market value (pre-percentage) of the line
    minerals: list[ReprocessMineral]
    leftover_units: int  # sub-batch remainder, priced at the ore's own price
    leftover_value: Decimal


def reprocess_line(
    quantity: int,
    portion_size: int,
    materials: list[tuple[int, int]],
    mineral_value: dict[int, Decimal | None],
    ore_unit_value: Decimal | None,
    *,
    yield_: Decimal = ORE_REFINE_YIELD,
) -> ReprocessResult | None:
    """Reprocess-price an ore line (ADR-0026) and report the breakdown:

    - **Whole refine batches** (`quantity // portion_size`) yield, per material,
      `base_qty × yield_` minerals each, valued at the mineral's market unit value (an
      unpriced mineral contributes 0).
    - The **leftover** below a full batch is valued at the ore's own market unit value.

    Returns the total market value (pre-percentage) plus the per-mineral breakdown, or
    `None` if nothing is priceable (total ≤ 0 → reject upstream).
    """
    batches = quantity // portion_size if portion_size > 0 else 0
    leftover = quantity - batches * portion_size

    minerals: list[ReprocessMineral] = []
    for material_id, base_qty in materials:
        qty = Decimal(batches) * Decimal(base_qty) * yield_
        mv = mineral_value.get(material_id)
        value = qty * mv if mv is not None else Decimal(0)
        minerals.append(ReprocessMineral(material_id, qty, mv, value))

    leftover_value = (
        Decimal(leftover) * ore_unit_value
        if leftover and ore_unit_value is not None
        else Decimal(0)
    )
    total = sum((m.value for m in minerals), Decimal(0)) + leftover_value
    if total <= 0:
        return None
    return ReprocessResult(
        total=total,
        minerals=minerals,
        leftover_units=leftover,
        leftover_value=leftover_value,
    )


def round_isk(value: Decimal) -> Decimal:
    """Round to 2 decimal places, banker's rounding (ADR-0021) — unbiased over many
    summed lines."""
    return value.quantize(_CENT, rounding=ROUND_HALF_EVEN)


def unit_price(unit_value: Decimal, percentage: Decimal) -> Decimal:
    """Apply the buyback percentage to a market unit value, rounded to 2 dp."""
    return round_isk(unit_value * percentage / Decimal(100))


def line_total(unit_price_value: Decimal, quantity: int) -> Decimal:
    """Total for a line, rounded to 2 dp."""
    return round_isk(unit_price_value * quantity)
