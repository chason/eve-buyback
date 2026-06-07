"""Per-corp buyback config (ADR-0007). Returns records; the application owns commit."""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import BuybackConfig
from app.data.records import BuybackConfigRecord
from app.domain.pricing import AggregateField, Basis


async def get_config(
    session: AsyncSession, corporation_id: int
) -> BuybackConfigRecord | None:
    row = await session.get(BuybackConfig, corporation_id)
    return BuybackConfigRecord.model_validate(row) if row is not None else None


async def upsert_config(
    session: AsyncSession,
    *,
    corporation_id: int,
    market_hub_id: int,
    default_basis: Basis,
    default_percentage: Decimal,
    aggregate_field: AggregateField,
) -> BuybackConfigRecord:
    row = await session.get(BuybackConfig, corporation_id)
    if row is None:
        row = BuybackConfig(corporation_id=corporation_id)
        session.add(row)
    row.market_hub_id = market_hub_id
    row.default_basis = default_basis
    row.default_percentage = default_percentage
    row.aggregate_field = aggregate_field
    await session.flush()
    await session.refresh(row)
    return BuybackConfigRecord.model_validate(row)
