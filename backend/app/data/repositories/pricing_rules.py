"""Pricing rule CRUD (ADR-0007). Returns records; the application owns commit.

Rules are addressed by their natural key — `(corporation_id, target_kind,
target_id)` — which is unique; no surrogate id is exposed (ADR-0022)."""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import PricingRule
from app.data.records import ConfiguredHubRecord, PricingRuleRecord
from app.domain.market import HubKind
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


async def list_hub_overrides(session: AsyncSession) -> list[ConfiguredHubRecord]:
    """Every per-rule market-hub override across all corps (ADR-0031/0034), with the
    owning corp. Only rules that actually set a hub (`market_hub_id` non-null) — those
    feed the background refresh's hub set alongside the corp-default hubs."""
    stmt = select(
        PricingRule.market_hub_id,
        PricingRule.market_hub_kind,
        PricingRule.market_region_id,
        PricingRule.corporation_id,
    ).where(PricingRule.market_hub_id.is_not(None))
    rows = (await session.execute(stmt)).all()
    return [
        ConfiguredHubRecord(
            hub_id=hub_id,
            # An override always saves its kind alongside the id; default defensively.
            kind=kind or "npc_station",
            region_id=region_id,
            corporation_id=corp_id,
        )
        for hub_id, kind, region_id, corp_id in rows
    ]


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
    folder: str | None = None,
    market_hub_id: str | None = None,
    market_hub_kind: HubKind | None = None,
    market_region_id: int | None = None,
    market_hub_name: str | None = None,
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
    row.folder = folder
    # PUT is full-replacement: a request without a hub clears the override.
    row.market_hub_id = market_hub_id
    row.market_hub_kind = market_hub_kind
    row.market_region_id = market_region_id
    row.market_hub_name = market_hub_name
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
