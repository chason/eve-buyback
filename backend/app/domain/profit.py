"""Pure profit aggregation for the "How we're doing" view (#159). No I/O: the use
case loads sale rows for a period and reduces them to facts; these functions fold
the facts into totals.

The two segmentation rules the view is built on (ADR-0043/0045):
- By channel — market fills, in-game contracts, and direct (off-game) deals are
  reported side by side, never blended into one anonymous number.
- By cost confidence — margin earned on stock with a measured cost stays separate
  from margin computed against an estimated (deemed) cost. Estimates propagate;
  they never silently launder into "measured".
"""

from dataclasses import dataclass
from decimal import Decimal

from app.domain.lots import SaleChannel

CHANNELS: tuple[SaleChannel, ...] = ("market", "contract", "direct")


@dataclass(frozen=True)
class SaleFact:
    """One sale row (one lot consumed) reduced to what profit needs. `unit_cost`
    is the sale-time COGS snapshot; `cost_is_estimated` its confidence."""

    channel: SaleChannel
    qty: int
    unit_proceeds: Decimal
    unit_cost: Decimal
    sales_tax: Decimal
    cost_is_estimated: bool


@dataclass(frozen=True)
class ChannelSummary:
    """One channel's fold: what came in, what the sold stock had cost, and the
    margin (revenue − tax − cost) before corp-level expenses."""

    channel: SaleChannel
    revenue: Decimal
    sales_tax: Decimal
    cost_of_goods: Decimal
    margin: Decimal


@dataclass(frozen=True)
class SalesSummary:
    """The whole period's sales fold. `measured_margin + estimated_margin ==
    margin`; the split keeps deemed-cost results visibly apart (ADR-0043)."""

    revenue: Decimal
    sales_tax: Decimal
    cost_of_goods: Decimal
    margin: Decimal
    measured_margin: Decimal
    estimated_margin: Decimal
    by_channel: tuple[ChannelSummary, ...]


def sale_margin(fact: SaleFact) -> Decimal:
    """One row's margin: proceeds − tax − cost of the goods sold."""
    return (
        fact.qty * fact.unit_proceeds
        - fact.sales_tax
        - fact.qty * fact.unit_cost
    )


def _channel_summary(channel: SaleChannel, facts: list[SaleFact]) -> ChannelSummary:
    rows = [f for f in facts if f.channel == channel]
    revenue = sum((f.qty * f.unit_proceeds for f in rows), Decimal(0))
    tax = sum((f.sales_tax for f in rows), Decimal(0))
    cost = sum((f.qty * f.unit_cost for f in rows), Decimal(0))
    return ChannelSummary(
        channel=channel,
        revenue=revenue,
        sales_tax=tax,
        cost_of_goods=cost,
        margin=revenue - tax - cost,
    )


def summarize_sales(facts: list[SaleFact]) -> SalesSummary:
    """Fold a period's sale facts into the view's totals. Channels with no sales
    are still present (zeroed) so the caller renders a stable shape."""
    channels = tuple(_channel_summary(c, facts) for c in CHANNELS)
    estimated = sum(
        (sale_margin(f) for f in facts if f.cost_is_estimated), Decimal(0)
    )
    margin = sum((c.margin for c in channels), Decimal(0))
    return SalesSummary(
        revenue=sum((c.revenue for c in channels), Decimal(0)),
        sales_tax=sum((c.sales_tax for c in channels), Decimal(0)),
        cost_of_goods=sum((c.cost_of_goods for c in channels), Decimal(0)),
        margin=margin,
        measured_margin=margin - estimated,
        estimated_margin=estimated,
        by_channel=channels,
    )
