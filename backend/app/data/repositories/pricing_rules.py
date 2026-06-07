"""Pricing rule CRUD (ADR-0007). Returns records; the application owns commit."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import PricingRule
from app.data.records import PricingRuleRecord
from app.domain.pricing import Basis, TargetKind


async def list_rules(
    session: AsyncSession, corporation_id: int
) -> list[PricingRuleRecord]:
    stmt = (
        select(PricingRule)
        .where(PricingRule.corporation_id == corporation_id)
        .order_by(PricingRule.id)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [PricingRuleRecord.model_validate(r) for r in rows]


async def get_rule(
    session: AsyncSession, rule_id: int
) -> PricingRuleRecord | None:
    row = await session.get(PricingRule, rule_id)
    return PricingRuleRecord.model_validate(row) if row is not None else None


async def get_rule_for_target(
    session: AsyncSession,
    *,
    corporation_id: int,
    target_kind: TargetKind,
    target_id: int,
) -> PricingRuleRecord | None:
    stmt = select(PricingRule).where(
        PricingRule.corporation_id == corporation_id,
        PricingRule.target_kind == target_kind,
        PricingRule.target_id == target_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    return PricingRuleRecord.model_validate(row) if row is not None else None


async def create_rule(
    session: AsyncSession,
    *,
    corporation_id: int,
    target_kind: TargetKind,
    target_id: int,
    basis: Basis | None,
    percentage: Decimal,
    enabled: bool,
) -> PricingRuleRecord:
    rule = PricingRule(
        corporation_id=corporation_id,
        target_kind=target_kind,
        target_id=target_id,
        basis=basis,
        percentage=percentage,
        enabled=enabled,
    )
    session.add(rule)
    await session.flush()
    await session.refresh(rule)
    return PricingRuleRecord.model_validate(rule)


async def update_rule(
    session: AsyncSession, rule_id: int, **fields
) -> PricingRuleRecord | None:
    """Patch the given fields on a rule. Returns None if it doesn't exist."""
    row = await session.get(PricingRule, rule_id)
    if row is None:
        return None
    for key, value in fields.items():
        setattr(row, key, value)
    await session.flush()
    await session.refresh(row)
    return PricingRuleRecord.model_validate(row)


async def delete_rule(session: AsyncSession, rule_id: int) -> bool:
    """Delete a rule; return False if it didn't exist."""
    row = await session.get(PricingRule, rule_id)
    if row is None:
        return False
    await session.delete(row)
    return True
