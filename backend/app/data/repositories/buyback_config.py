"""Per-corp buyback config (ADR-0007). Keyed by the corporation UUID (ADR-0025).
Returns records; the application owns commit."""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import BuybackConfig
from app.data.records import BuybackConfigRecord
from app.domain.market import HubKind
from app.domain.pricing import AggregateField, Basis


async def get_config(
    session: AsyncSession, corporation_id: uuid.UUID
) -> BuybackConfigRecord | None:
    row = await _row(session, corporation_id)
    return BuybackConfigRecord.model_validate(row) if row is not None else None


async def upsert_config(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    market_hub_id: int,
    default_basis: Basis,
    default_percentage: Decimal,
    aggregate_field: AggregateField,
    default_accepted: bool = True,
    market_hub_kind: HubKind = "npc_station",
    market_region_id: int | None = None,
    market_hub_name: str | None = None,
) -> BuybackConfigRecord:
    row = await _row(session, corporation_id)
    if row is None:
        row = BuybackConfig(corporation_id=corporation_id)
        session.add(row)
    row.market_hub_id = market_hub_id
    row.market_hub_kind = market_hub_kind
    row.market_region_id = market_region_id
    row.market_hub_name = market_hub_name
    row.default_basis = default_basis
    row.default_percentage = default_percentage
    row.aggregate_field = aggregate_field
    row.default_accepted = default_accepted
    await session.flush()
    await session.refresh(row)
    return BuybackConfigRecord.model_validate(row)


async def _row(
    session: AsyncSession, corporation_id: uuid.UUID
) -> BuybackConfig | None:
    stmt = select(BuybackConfig).where(
        BuybackConfig.corporation_id == corporation_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()
