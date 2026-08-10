"""The "How we're doing" profit view (#159, ADR-0043/0045): what a period's sales
brought in, what the sold stock had cost, and what selling it cost on top — folded
by the pure domain (`domain/profit`) from the frozen sale-time facts, so the view
never shifts when later write-downs floor the lots that fed it.

Read-only and gated: the accounting entitlement is required (ADR-0042); the
manager gate sits at the interface.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.application import entitlements as entitlements_app
from app.application.corporations import get_registered_corporation
from app.data.records import SaleRecord
from app.data.repositories import expenses as expenses_repo
from app.data.repositories import sales as sales_repo
from app.domain.lots import SaleChannel
from app.domain.profit import SaleFact, summarize_sales

# UI groupings for expense kinds (the vocabulary map: "market fees" vs everything
# else); `write_down` stands alone as its own line.
_FEE_KINDS = frozenset({"broker_fee", "relist_fee"})
_OTHER_KINDS = frozenset({"hauling", "other"})


class ChannelProfitView(BaseModel):
    """One sales channel's slice: revenue, tax, cost of the goods that left, and
    the margin those leave behind. `sale_count` counts sale EVENTS (a fill split
    across lots is one sale, not several)."""

    channel: SaleChannel
    revenue: Decimal
    sales_tax: Decimal
    cost_of_goods: Decimal
    margin: Decimal
    sale_count: int


class ProfitView(BaseModel):
    """The whole period folded (#159). `margin` is revenue − tax − cost of goods;
    `profit` subtracts the period's expenses on top. `measured_margin` /
    `estimated_margin` keep deemed-cost results visibly apart (ADR-0043) — they
    sum to `margin`. Expenses are real ISK, never estimated."""

    since: datetime | None
    until: datetime | None
    revenue: Decimal
    sales_tax: Decimal
    cost_of_goods: Decimal
    margin: Decimal
    measured_margin: Decimal
    estimated_margin: Decimal
    fees: Decimal
    write_downs: Decimal
    other_expenses: Decimal
    profit: Decimal
    sale_count: int
    channels: list[ChannelProfitView]


def _count_events(records: list[SaleRecord]) -> dict[SaleChannel, int]:
    """Sale EVENTS per channel: rows sharing (channel, external_ref) are one fill
    or contract split across lots; manual rows (ref None) count one each."""
    keys = set()
    for record in records:
        ref = record.external_ref if record.external_ref is not None else record.id
        keys.add((record.channel, ref))
    counts: dict[SaleChannel, int] = {}
    for channel, _ in keys:
        counts[channel] = counts.get(channel, 0) + 1
    return counts


def _sale_fact(record: SaleRecord) -> SaleFact:
    return SaleFact(
        channel=record.channel,
        qty=record.qty,
        unit_proceeds=record.unit_proceeds,
        unit_cost=record.unit_cost,
        sales_tax=record.sales_tax,
        cost_is_estimated=record.cost_is_estimated,
    )


async def get_profit(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    since: datetime | None = None,
    until: datetime | None = None,
) -> ProfitView:
    """Fold one period's ledger into the profit view: sales sold in [since, until)
    and expenses incurred in it. Either bound may be None (open-ended)."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )

    records = await sales_repo.list_between(
        session, corporation_id=corp.id, since=since, until=until
    )
    summary = summarize_sales([_sale_fact(r) for r in records])
    counts = _count_events(records)

    expenses = await expenses_repo.list_between(
        session, corporation_id=corp.id, since=since, until=until
    )
    fees = sum((e.amount for e in expenses if e.kind in _FEE_KINDS), Decimal(0))
    write_downs = sum(
        (e.amount for e in expenses if e.kind == "write_down"), Decimal(0)
    )
    other = sum((e.amount for e in expenses if e.kind in _OTHER_KINDS), Decimal(0))

    return ProfitView(
        since=since,
        until=until,
        revenue=summary.revenue,
        sales_tax=summary.sales_tax,
        cost_of_goods=summary.cost_of_goods,
        margin=summary.margin,
        measured_margin=summary.measured_margin,
        estimated_margin=summary.estimated_margin,
        fees=fees,
        write_downs=write_downs,
        other_expenses=other,
        profit=summary.margin - fees - write_downs - other,
        sale_count=sum(counts.values()),
        channels=[
            ChannelProfitView(
                channel=c.channel,
                revenue=c.revenue,
                sales_tax=c.sales_tax,
                cost_of_goods=c.cost_of_goods,
                margin=c.margin,
                sale_count=counts.get(c.channel, 0),
            )
            for c in summary.by_channel
        ],
    )
