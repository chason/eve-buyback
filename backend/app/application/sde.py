"""SDE seed use case (ADR-0009). Pulls reference rows from the SDE source, keeps
only the market-tradeable types, upserts them, and stamps the import. Owns the
unit of work (commit)."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.records import SdeMetadataRecord
from app.data.repositories import sde as sde_repo
from app.domain.pricing import ORE_CATEGORY_ID
from app.plugins.sde_source import SdeSource


async def seed_reference_data(
    session: AsyncSession, source: SdeSource, *, source_label: str
) -> SdeMetadataRecord:
    """Seed `sde_market_groups`, `sde_types`, and ore reprocessing yields
    (`sde_type_materials`) from `source`, idempotently.

    Market groups are imported in full (the whole tree is needed for rule
    resolution); types are filtered to **published items that have a market
    group** — the things a buyback can quote — to keep the table small. Each type
    is tagged with its `category_id` (via the group→category map) so ores
    (category 25) can be reprocess-priced (ADR-0026); we then seed **only ore
    types'** material yields.
    """
    groups = await source.fetch_market_groups()
    group_rows = [g.model_dump() for g in groups]
    group_count = await sde_repo.bulk_upsert_market_groups(session, group_rows)

    category_of = await source.fetch_group_categories()
    types = await source.fetch_types()
    kept = [t for t in types if t.published and t.market_group_id is not None]
    type_rows = [
        {**t.model_dump(), "category_id": category_of.get(t.group_id)}
        for t in kept
    ]
    type_count = await sde_repo.bulk_upsert_types(session, type_rows)

    # Reprocessing yields: keep only ore (category 25) types' materials.
    ore_type_ids = {
        t.type_id for t in kept if category_of.get(t.group_id) == ORE_CATEGORY_ID
    }
    materials = await source.fetch_type_materials()
    material_rows = [
        m.model_dump() for m in materials if m.type_id in ore_type_ids
    ]
    await sde_repo.bulk_upsert_type_materials(session, material_rows)

    metadata = await sde_repo.set_metadata(
        session,
        source=source_label,
        type_count=type_count,
        market_group_count=group_count,
        imported_at=datetime.now(UTC),
    )
    await session.commit()
    return metadata
