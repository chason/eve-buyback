"""Aggregation of raw ESI orders into Fuzzwork-shaped figures (domain/aggregates).

Hand-computed Decimal vectors — this is the correctness core of ESI pricing
(ADR-0028). The percentile partial-boundary case is the load-bearing one.
"""

from decimal import Decimal

from app.domain.aggregates import (
    RawOrder,
    aggregate_orders,
    aggregate_side,
)


def _sell(price: str, volume: int) -> RawOrder:
    return RawOrder(price=Decimal(price), volume_remain=volume, is_buy_order=False)


def _buy(price: str, volume: int) -> RawOrder:
    return RawOrder(price=Decimal(price), volume_remain=volume, is_buy_order=True)


def test_empty_side_is_all_zero_with_no_orders():
    side = aggregate_side([], is_buy=False)
    assert side.order_count == 0
    assert side.volume == Decimal(0)
    assert side.weighted_average == side.percentile == side.median == Decimal(0)


def test_single_order_collapses_to_its_price():
    side = aggregate_side([_sell("100", 50)], is_buy=False)
    assert side.order_count == 1
    assert side.volume == Decimal(50)
    assert side.max == side.min == Decimal("100")
    assert side.weighted_average == Decimal("100")
    assert side.median == Decimal("100")
    assert side.percentile == Decimal("100")


def test_weighted_average_is_volume_weighted_exact():
    # (100*1 + 200*3) / 4 = 175 exactly (no float drift).
    side = aggregate_side([_sell("100", 1), _sell("200", 3)], is_buy=False)
    assert side.weighted_average == Decimal("175")


def test_volume_weighted_median_crosses_mid_order():
    # vols 10/10/10 (total 30, mark 15): cumulative 10 < 15, then 20 >= 15 -> 2nd order.
    orders = [_sell("100", 10), _sell("200", 10), _sell("300", 10)]
    side = aggregate_side(orders, is_buy=False)
    assert side.median == Decimal("200")


def test_percentile_partial_boundary_order():
    # Sell vols 1 then 100 (total 101); 5% target = 5.05. The cheapest order (price
    # 100, vol 1) is fully taken, then 4.05 of the next (price 110): percentile =
    # (100*1 + 110*4.05) / 5.05 = 545.50 / 5.05 = 108.019801...
    # Distinguishes the correct algorithm from "best order only" (100) or
    # "average all" ((100+110*100)/101 = 109.90).
    side = aggregate_side([_sell("100", 1), _sell("110", 100)], is_buy=False)
    assert side.percentile.quantize(Decimal("0.0001")) == Decimal("108.0198")


def test_buy_side_orders_best_is_highest_price():
    # Buyers: best = highest price. Descending order drives median/percentile.
    side = aggregate_side([_buy("90", 10), _buy("100", 10)], is_buy=True)
    assert side.max == Decimal("100")
    assert side.min == Decimal("90")
    # mark = 10: first (best=100) order's cumulative volume hits it -> median 100.
    assert side.median == Decimal("100")
    # 5% of 20 = 1.0, all from the best (100) order.
    assert side.percentile == Decimal("100")


def test_zero_volume_orders_fall_back_to_mean():
    # All volume_remain 0 -> volume 0, but the side still carries a price (mean).
    orders = [_sell("100", 0), _sell("300", 0)]
    side = aggregate_side(orders, is_buy=False)
    assert side.volume == Decimal(0)
    assert side.order_count == 2
    assert side.weighted_average == Decimal("200")  # plain mean
    assert side.percentile == Decimal("200")


def test_aggregate_orders_splits_buy_and_sell():
    book = aggregate_orders(
        [_buy("90", 5), _sell("100", 5), _sell("110", 5)]
    )
    assert book.buy.order_count == 1
    assert book.buy.max == Decimal("90")
    assert book.sell.order_count == 2
    assert book.sell.min == Decimal("100")
    assert book.sell.max == Decimal("110")


def test_all_one_side_zeroes_the_other():
    book = aggregate_orders([_buy("90", 5), _buy("80", 5)])
    assert book.buy.order_count == 2
    assert book.sell.order_count == 0  # rejected upstream by the order_count gate
    assert book.sell.weighted_average == Decimal(0)
