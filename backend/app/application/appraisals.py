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
from app.application.corporations import get_registered_corporation
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
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import pricing_rules as rules_repo
from app.data.repositories import sde as sde_repo
from app.domain import pricing as pricing_domain
from app.domain.ids import generate_appraisal_id
from app.domain.paste import parse_paste
from app.domain.roles import role_at_least
from app.plugins.fuzzwork import FuzzworkClient


@dataclass(frozen=True)
class AppraisalItem:
    type_id: int
    quantity: int


@dataclass(frozen=True)
class _WorkItem:
    """A line to price. `type_id is None` means an unresolved pasted name —
    `fallback_name` carries it for the rejected line."""

    type_id: int | None
    fallback_name: str | None
    quantity: int
    reason: str | None = None


async def create_appraisal(
    session: AsyncSession,
    fuzzwork: FuzzworkClient,
    *,
    user: AuthenticatedUser,
    items: list[AppraisalItem],
    paste: str | None,
    now: datetime,
) -> AppraisalRecord:
    corp = await get_registered_corporation(session, user.corporation_id)  # 404 if not
    config = await get_config(session, user.corporation_id)
    hub_id = config.market_hub_id

    work = await _gather_items(session, items, paste)
    if not work:
        raise EmptyAppraisal()

    # Reference data + rules + prices, fetched once for the whole appraisal.
    type_ids = list({w.type_id for w in work if w.type_id is not None})
    types = await sde_repo.get_types(session, type_ids)
    parent_of = {
        g.market_group_id: g.parent_id
        for g in await sde_repo.list_market_groups(session)
    }
    rules = await rules_repo.list_rules(session, corp.id)
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
        session, fuzzwork, hub_id=hub_id, type_ids=type_ids, now=now
    )
    price_by_id = {p.type_id: p for p in prices}

    lines: list[dict] = []
    accepted_total = Decimal("0")
    rejected_count = 0
    for w in work:
        line = _compute_line(
            w, config, types, price_by_id, type_rules, group_rules, parent_of
        )
        lines.append(line)
        if line["status"] == "accepted":
            accepted_total += line["line_total"]
        else:
            rejected_count += 1

    record = await appraisals_repo.create_appraisal(
        session,
        public_id=generate_appraisal_id(),
        corporation_id=corp.id,
        created_by_character_id=user.character_id,
        market_hub_id=hub_id,
        accepted_total=accepted_total,
        rejected_count=rejected_count,
        request_json={
            "items": [
                {"type_id": it.type_id, "quantity": it.quantity} for it in items
            ],
            "paste": paste,
        },
        lines=lines,
    )
    await session.commit()
    return record


async def _gather_items(
    session: AsyncSession, items: list[AppraisalItem], paste: str | None
) -> list[_WorkItem]:
    """Combine structured items with parsed + name-resolved paste lines."""
    work = [
        _WorkItem(type_id=it.type_id, fallback_name=None, quantity=it.quantity)
        for it in items
    ]
    if paste and paste.strip():
        parsed = parse_paste(paste)
        by_name = await sde_repo.get_types_by_names(
            session, [p.name for p in parsed]
        )
        for p in parsed:
            matches = by_name.get(p.name.lower(), [])
            if len(matches) == 1:
                work.append(
                    _WorkItem(
                        type_id=matches[0].type_id,
                        fallback_name=None,
                        quantity=p.quantity,
                    )
                )
            else:
                # 0 matches → unknown; >1 → an ambiguous EVE-SDE duplicate name.
                # Never silently guess which type the paster meant — reject it.
                reason = (
                    f"Ambiguous name ({len(matches)} matches)"
                    if matches
                    else "Unknown item"
                )
                work.append(
                    _WorkItem(
                        type_id=None,
                        fallback_name=p.name,
                        quantity=p.quantity,
                        reason=reason,
                    )
                )
    return work


async def get_appraisal(
    session: AsyncSession, *, corporation_id: int, public_id: str
) -> AppraisalRecord:
    corp = await corporations_repo.get_by_eve_id(session, corporation_id)
    record = await appraisals_repo.get_by_public_id(session, public_id)
    # 404 for missing OR cross-corp — don't leak existence (ADR-0014).
    if record is None or corp is None or record.corporation_id != corp.id:
        raise AppraisalNotFound()
    return record


async def list_appraisals(
    session: AsyncSession, *, user: AuthenticatedUser
) -> list[AppraisalSummaryRecord]:
    corp = await corporations_repo.get_by_eve_id(session, user.corporation_id)
    if corp is None:
        return []
    if role_at_least(user.role, "manager"):
        return await appraisals_repo.list_for_corp(session, corp.id)
    return await appraisals_repo.list_for_character(
        session, corp.id, user.character_id
    )


def _compute_line(
    w: _WorkItem,
    config: BuybackConfigRecord,
    types: dict[int, SdeTypeRecord],
    price_by_id: dict[int, MarketPriceRecord],
    type_rules: dict[int, pricing_domain.RuleSpec],
    group_rules: dict[int, pricing_domain.RuleSpec],
    parent_of: dict[int, int | None],
) -> dict:
    if w.type_id is None:
        return _rejected(
            None, w.fallback_name or "Unknown", w.quantity, w.reason or "Unknown item"
        )

    sde_type = types.get(w.type_id)
    if sde_type is None:
        return _rejected(w.type_id, f"Type {w.type_id}", w.quantity, "Unknown item")

    resolved = pricing_domain.resolve_rule(
        w.type_id,
        sde_type.market_group_id,
        type_rules=type_rules,
        group_rules=group_rules,
        parent_of=parent_of,
        default_basis=config.default_basis,
        default_percentage=config.default_percentage,
    )

    price = price_by_id.get(w.type_id)
    if price is None:
        return _rejected(w.type_id, sde_type.name, w.quantity, "No market data")

    agg = config.aggregate_field
    buy = getattr(price, f"buy_{agg}") if price.buy_order_count > 0 else None
    sell = getattr(price, f"sell_{agg}") if price.sell_order_count > 0 else None
    unit_value = pricing_domain.select_aggregate(buy, sell, resolved.basis)
    if unit_value is None or unit_value <= 0:
        return _rejected(w.type_id, sde_type.name, w.quantity, f"No {resolved.basis} orders")

    up = pricing_domain.unit_price(unit_value, resolved.percentage)
    lt = pricing_domain.line_total(up, w.quantity)
    return {
        "type_id": w.type_id,
        "type_name": sde_type.name,
        "quantity": w.quantity,
        "status": "accepted",
        "basis": resolved.basis,
        "percentage": resolved.percentage,
        "unit_value": unit_value,
        "unit_price": up,
        "line_total": lt,
        "reason": None,
    }


def _rejected(
    type_id: int | None, type_name: str, quantity: int, reason: str
) -> dict:
    return {
        "type_id": type_id,
        "type_name": type_name,
        "quantity": quantity,
        "status": "rejected",
        "basis": None,
        "percentage": None,
        "unit_value": None,
        "unit_price": None,
        "line_total": Decimal("0"),
        "reason": reason,
    }
