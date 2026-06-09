"""Aggregate raw EVE market orders into the same 7 per-side figures Fuzzwork
returns (ADR-0028), so ESI-sourced prices are interchangeable with Fuzzwork's in
the `(hub_id, type_id)` price cache. Pure, `Decimal`-only, no I/O.

Definitions (matching Fuzzwork's semantics so cached rows are source-agnostic):

- **max / min** — the highest / lowest order price (plain extremes, side-agnostic).
- **weighted_average** — ``Σ(price · volume) / Σ(volume)``.
- **median** — volume-weighted: the price at the 50%-cumulative-volume mark,
  scanning best→worst (sell ascending, buy descending).
- **percentile** — the volume-weighted average price of the **best 5% of volume** on
  the side (cheapest 5% of sell volume / dearest 5% of buy volume), including only
  the fractional `take` of the order that straddles the boundary. Manipulation-
  resistant: a single tiny "best" order can't swing it.
- **volume** — ``Σ volume_remain``. **order_count** — number of orders.

An empty side yields an all-zero aggregate with ``order_count == 0`` (as Fuzzwork
does); the appraisal layer's ``order_count > 0`` gate then treats it as "no orders"
and rejects the line.
"""

from dataclasses import dataclass
from decimal import Decimal

# Fraction of volume that defines the "best orders" window for the percentile.
PERCENTILE_FRACTION = Decimal("0.05")


@dataclass(frozen=True)
class RawOrder:
    """One market order, as handed up from the ESI plugin (price already Decimal)."""

    price: Decimal
    volume_remain: int
    is_buy_order: bool


@dataclass(frozen=True)
class SideAggregate:
    """One side (buy or sell) — mirrors `plugins.fuzzwork.FuzzworkSide`'s fields so
    the market use case can build a cache row from either source uniformly."""

    weighted_average: Decimal
    max: Decimal
    min: Decimal
    median: Decimal
    percentile: Decimal
    volume: Decimal
    order_count: int


@dataclass(frozen=True)
class OrderBookAggregate:
    """Both sides — mirrors `plugins.fuzzwork.FuzzworkAggregate`."""

    buy: SideAggregate
    sell: SideAggregate


_ZERO_SIDE = SideAggregate(
    weighted_average=Decimal(0),
    max=Decimal(0),
    min=Decimal(0),
    median=Decimal(0),
    percentile=Decimal(0),
    volume=Decimal(0),
    order_count=0,
)


def aggregate_orders(orders: list[RawOrder]) -> OrderBookAggregate:
    """Split a type's orders into buy/sell and aggregate each side."""
    buy = [o for o in orders if o.is_buy_order]
    sell = [o for o in orders if not o.is_buy_order]
    return OrderBookAggregate(
        buy=aggregate_side(buy, is_buy=True),
        sell=aggregate_side(sell, is_buy=False),
    )


def aggregate_side(orders: list[RawOrder], *, is_buy: bool) -> SideAggregate:
    """Reduce one side's orders to the 7 figures. `is_buy` only sets the best→worst
    ordering (buyers favour the highest price, sellers the lowest)."""
    if not orders:
        return _ZERO_SIDE

    ordered = sorted(orders, key=lambda o: o.price, reverse=is_buy)
    prices = [o.price for o in ordered]
    max_price = max(prices)
    min_price = min(prices)
    total_volume = sum(o.volume_remain for o in ordered)

    if total_volume <= 0:
        # Degenerate: every order has zero remaining volume. Fall back to a plain
        # mean so the side still carries a price; order_count > 0 keeps it usable.
        mean = sum(prices, Decimal(0)) / Decimal(len(prices))
        return SideAggregate(
            weighted_average=mean,
            max=max_price,
            min=min_price,
            median=mean,
            percentile=mean,
            volume=Decimal(0),
            order_count=len(orders),
        )

    total_volume_d = Decimal(total_volume)
    weighted_average = (
        sum((o.price * Decimal(o.volume_remain) for o in ordered), Decimal(0))
        / total_volume_d
    )
    median = _volume_mark_price(ordered, total_volume_d / Decimal(2))
    percentile = _volume_weighted_top(ordered, total_volume_d * PERCENTILE_FRACTION)

    return SideAggregate(
        weighted_average=weighted_average,
        max=max_price,
        min=min_price,
        median=median,
        percentile=percentile,
        volume=total_volume_d,
        order_count=len(orders),
    )


def _volume_mark_price(ordered: list[RawOrder], mark: Decimal) -> Decimal:
    """Price of the first order whose cumulative volume reaches `mark` (best→worst)."""
    cumulative = Decimal(0)
    for order in ordered:
        cumulative += Decimal(order.volume_remain)
        if cumulative >= mark:
            return order.price
    return ordered[-1].price


def _volume_weighted_top(ordered: list[RawOrder], target: Decimal) -> Decimal:
    """Volume-weighted average price over the best `target` units of volume, counting
    only the fractional `take` of the order that straddles the boundary."""
    if target <= 0:
        return ordered[0].price
    taken = Decimal(0)
    numerator = Decimal(0)
    for order in ordered:
        take = min(Decimal(order.volume_remain), target - taken)
        if take <= 0:
            continue
        numerator += order.price * take
        taken += take
        if taken >= target:
            break
    return numerator / taken if taken > 0 else ordered[0].price
