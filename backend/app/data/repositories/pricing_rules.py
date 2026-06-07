"""Pricing rule CRUD (ADR-0007). Returns records; the application owns commit.

Rules are addressed by their natural key — `(corporation_id, target_kind,
target_id)` — which is unique; no surrogate id is exposed (ADR-0022)."""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import PricingRule
from app.data.records import PricingRuleRecord
from app.domain.pricing import Basis, TargetKind


async def list_rules(
    session: AsyncSession, corporation_id: uuid.UUID
) -> list[PricingRuleRecord]:
    stmt = (
        select(PricingRule)
        .where(PricingRule.corporation_id == corporation_id)
        .order_by(PricingRule.id)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [PricingRuleRecord.model_validate(r) for r in rows]


async def upsert_rule(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    target_kind: TargetKind,
    target_id: int,
    basis: Basis | None,
    percentage: Decimal,
    enabled: bool,
    reprocess: bool,
    compressed_only: bool,
    accepted: bool,
) -> tuple[PricingRuleRecord, bool]:
    """Create or replace the corp's rule for a target. Returns `(record, created)`.

    A portable get-then-set (like `buyback_config.upsert_config`) — no
    dialect-specific `ON CONFLICT`; safe on SQLite and Postgres alike. Fine for this
    low-concurrency, manager-driven path."""
    row = await _get_row(
        session,
        corporation_id=corporation_id,
        target_kind=target_kind,
        target_id=target_id,
    )
    created = row is None
    if row is None:
        row = PricingRule(
            corporation_id=corporation_id,
            target_kind=target_kind,
            target_id=target_id,
        )
        session.add(row)
    row.basis = basis
    row.percentage = percentage
    row.enabled = enabled
    row.reprocess = reprocess
    row.compressed_only = compressed_only
    row.accepted = accepted
    await session.flush()
    await session.refresh(row)
    return PricingRuleRecord.model_validate(row), created


async def delete_rule(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    target_kind: TargetKind,
    target_id: int,
) -> bool:
    """Delete the corp's rule for a target; return False if it didn't exist."""
    row = await _get_row(
        session,
        corporation_id=corporation_id,
        target_kind=target_kind,
        target_id=target_id,
    )
    if row is None:
        return False
    await session.delete(row)
    return True


async def _get_row(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    target_kind: TargetKind,
    target_id: int,
) -> PricingRule | None:
    stmt = select(PricingRule).where(
        PricingRule.corporation_id == corporation_id,
        PricingRule.target_kind == target_kind,
        PricingRule.target_id == target_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()
