"""SDE seed use case (ADR-0009). Pulls reference rows from the SDE source, keeps
only the market-tradeable types, upserts them, and stamps the import. Owns the
unit of work (commit)."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.records import SdeMetadataRecord
from app.data.repositories import sde as sde_repo
from app.plugins.sde_source import SdeSource


async def seed_reference_data(
    session: AsyncSession, source: SdeSource, *, source_label: str
) -> SdeMetadataRecord:
    """Seed `sde_market_groups` and `sde_types` from `source`, idempotently.

    Market groups are imported in full (the whole tree is needed for rule
    resolution); types are filtered to **published items that have a market
    group** — the things a buyback can quote — to keep the table small.
    """
    groups = await source.fetch_market_groups()
    group_rows = [g.model_dump() for g in groups]
    group_count = await sde_repo.bulk_upsert_market_groups(session, group_rows)

    types = await source.fetch_types()
    type_rows = [
        t.model_dump()
        for t in types
        if t.published and t.market_group_id is not None
    ]
    type_count = await sde_repo.bulk_upsert_types(session, type_rows)

    metadata = await sde_repo.set_metadata(
        session,
        source=source_label,
        type_count=type_count,
        market_group_count=group_count,
        imported_at=datetime.now(UTC),
    )
    await session.commit()
    return metadata
