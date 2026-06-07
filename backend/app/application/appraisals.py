"""Appraisal computation + persistence (ADR-0014 immutable snapshot, ADR-0007
resolution, ADR-0021 computation). Orchestration only — the money math is in
`domain/pricing.py`, the prices come from the market use case, and persistence is in
the appraisals repository. Owns the commit."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.application import market
from app.application.auth import AuthenticatedUser
from app.application.errors import AppraisalNotFound, EmptyAppraisal
from app.application.pricing import get_config
from app.data.records import (
    AppraisalRecord,
    AppraisalSummaryRecord,
    BuybackConfigRecord,
    MarketPriceRecord,
    SdeTypeRecord,
)
from app.data.repositories import appraisals as appraisals_repo
from app.data.repositories import pricing_rules as rules_repo
from app.data.repositories import sde as sde_repo
from app.domain import pricing as pricing_domain
from app.domain.ids import generate_appraisal_id
from app.domain.roles import role_at_least
from app.plugins.fuzzwork import FuzzworkClient


@dataclass(frozen=True)
class AppraisalItem:
    type_id: int
    quantity: int


async def create_appraisal(
    session: AsyncSession,
    fuzzwork: FuzzworkClient,
    *,
    user: AuthenticatedUser,
    items: list[AppraisalItem],
    now: datetime,
) -> AppraisalRecord:
    if not items:
        raise EmptyAppraisal()
    config = await get_config(session, user.corporation_id)  # 404 if unregistered
    hub_id = config.market_hub_id

    # Reference data + rules + prices, fetched once for the whole appraisal.
    types = await sde_repo.get_types(session, list({it.type_id for it in items}))
    parent_of = {
        g.market_group_id: g.parent_id
        for g in await sde_repo.list_market_groups(session)
    }
    rules = await rules_repo.list_rules(session, user.corporation_id)
    type_rules = {
        r.target_id: pricing_domain.RuleSpec(r.basis, r.percentage)
        for r in rules
        if r.enabled and r.target_kind == "type"
    }
    group_rules = {
        r.target_id: pricing_domain.RuleSpec(r.basis, r.percentage)
        for r in rules
        if r.enabled and r.target_kind == "market_group"
    }
    prices = await market.get_market_prices(
        session, fuzzwork, hub_id=hub_id, type_ids=list(types.keys()), now=now
    )
    price_by_id = {p.type_id: p for p in prices}

    lines: list[dict] = []
    accepted_total = Decimal("0")
    rejected_count = 0
    for item in items:
        line = _compute_line(
            item, config, types, price_by_id, type_rules, group_rules, parent_of
        )
        lines.append(line)
        if line["status"] == "accepted":
            accepted_total += line["line_total"]
        else:
            rejected_count += 1

    record = await appraisals_repo.create_appraisal(
        session,
        public_id=generate_appraisal_id(),
        corporation_id=user.corporation_id,
        created_by_character_id=user.character_id,
        market_hub_id=hub_id,
        accepted_total=accepted_total,
        rejected_count=rejected_count,
        request_json={
            "items": [
                {"type_id": it.type_id, "quantity": it.quantity} for it in items
            ]
        },
        lines=lines,
    )
    await session.commit()
    return record


async def get_appraisal(
    session: AsyncSession, *, corporation_id: int, public_id: str
) -> AppraisalRecord:
    record = await appraisals_repo.get_by_public_id(session, public_id)
    # 404 for missing OR cross-corp — don't leak existence (ADR-0014).
    if record is None or record.corporation_id != corporation_id:
        raise AppraisalNotFound()
    return record


async def list_appraisals(
    session: AsyncSession, *, user: AuthenticatedUser
) -> list[AppraisalSummaryRecord]:
    if role_at_least(user.role, "manager"):
        return await appraisals_repo.list_for_corp(session, user.corporation_id)
    return await appraisals_repo.list_for_character(
        session, user.corporation_id, user.character_id
    )


def _compute_line(
    item: AppraisalItem,
    config: BuybackConfigRecord,
    types: dict[int, SdeTypeRecord],
    price_by_id: dict[int, MarketPriceRecord],
    type_rules: dict[int, pricing_domain.RuleSpec],
    group_rules: dict[int, pricing_domain.RuleSpec],
    parent_of: dict[int, int | None],
) -> dict:
    sde_type = types.get(item.type_id)
    if sde_type is None:
        return _rejected(item, f"Type {item.type_id}", "Unknown item")

    resolved = pricing_domain.resolve_rule(
        item.type_id,
        sde_type.market_group_id,
        type_rules=type_rules,
        group_rules=group_rules,
        parent_of=parent_of,
        default_basis=config.default_basis,
        default_percentage=config.default_percentage,
    )

    price = price_by_id.get(item.type_id)
    if price is None:
        return _rejected(item, sde_type.name, "No market data")

    agg = config.aggregate_field
    buy = getattr(price, f"buy_{agg}") if price.buy_order_count > 0 else None
    sell = getattr(price, f"sell_{agg}") if price.sell_order_count > 0 else None
    unit_value = pricing_domain.select_aggregate(buy, sell, resolved.basis)
    if unit_value is None or unit_value <= 0:
        return _rejected(item, sde_type.name, f"No {resolved.basis} orders")

    up = pricing_domain.unit_price(unit_value, resolved.percentage)
    lt = pricing_domain.line_total(up, item.quantity)
    return {
        "type_id": item.type_id,
        "type_name": sde_type.name,
        "quantity": item.quantity,
        "status": "accepted",
        "basis": resolved.basis,
        "percentage": resolved.percentage,
        "unit_value": unit_value,
        "unit_price": up,
        "line_total": lt,
        "reason": None,
    }


def _rejected(item: AppraisalItem, type_name: str, reason: str) -> dict:
    return {
        "type_id": item.type_id,
        "type_name": type_name,
        "quantity": item.quantity,
        "status": "rejected",
        "basis": None,
        "percentage": None,
        "unit_value": None,
        "unit_price": None,
        "line_total": Decimal("0"),
        "reason": reason,
    }
