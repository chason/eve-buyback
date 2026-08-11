"""SDE seed use case (ADR-0009). Pulls reference rows from the SDE source, keeps
only the market-tradeable types, upserts them, and stamps the import. Owns the
unit of work (commit)."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.records import SdeMetadataRecord
from app.data.repositories import sde as sde_repo
from app.plugins.sde_source import SdeSource

# What the seed covers, stamped into `sde_metadata` on each import. Bump it whenever
# the seed's coverage changes (new table, wider filter): `seed_if_needed` — which the
# container entrypoint runs on every boot — re-seeds when the stamp is older, so a
# deploy picks the change up without anyone remembering a manual re-seed.
#   1: implicit pre-versioning coverage (ore-only `sde_type_materials`, NULL stamp)
#   2: `sde_type_materials` for every kept type, not just ore (ADR-0047, #197)
SEED_VERSION = 2


async def seed_reference_data(
    session: AsyncSession, source: SdeSource, *, source_label: str
) -> SdeMetadataRecord:
    """Seed `sde_market_groups`, `sde_types`, and reprocessing yields
    (`sde_type_materials`) from `source`, idempotently.

    Market groups are imported in full (the whole tree is needed for rule
    resolution); types are filtered to **published items that have a market
    group** — the things a buyback can quote — to keep the table small. Each type
    is tagged with its `category_id` (via the group→category map) so ores
    (category 25) can be reprocess-priced (ADR-0026). Material yields are seeded
    for **every kept type** — reprocessing lots is source-agnostic (ADR-0047), so
    the yields must cover modules, ships, and salvage, not just ore.
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

    # Reprocessing yields for every kept type (ADR-0047): a type with no rows here
    # genuinely cannot be reprocessed, which is what gates the UI's record action.
    kept_type_ids = {t.type_id for t in kept}
    materials = await source.fetch_type_materials()
    material_rows = [
        m.model_dump() for m in materials if m.type_id in kept_type_ids
    ]
    await sde_repo.bulk_upsert_type_materials(session, material_rows)

    # NPC stations for the hub picker (ADR-0028): join each station's system name.
    system_of = await source.fetch_systems()
    stations = await source.fetch_stations()
    station_rows = [
        {
            "station_id": s.station_id,
            "name": s.name,
            "system_name": system_of.get(s.system_id, ""),
            "region_id": s.region_id,
        }
        for s in stations
    ]
    await sde_repo.bulk_upsert_stations(session, station_rows)

    metadata = await sde_repo.set_metadata(
        session,
        source=source_label,
        type_count=type_count,
        market_group_count=group_count,
        imported_at=datetime.now(UTC),
        seed_version=SEED_VERSION,
    )
    await session.commit()
    return metadata


async def seed_if_needed(
    session: AsyncSession, source: SdeSource, *, source_label: str
) -> SdeMetadataRecord | None:
    """Seed only when the SDE looks incomplete (any reference table empty) or was
    imported by an older seed (`seed_version` stamp below `SEED_VERSION`). Returns
    the import metadata if it seeded, or `None` if it was already complete and
    current. Lets the container entrypoint auto-seed on first deploy — and re-seed
    when a deploy widens the seed's coverage — without re-downloading on every
    restart."""
    if await sde_repo.is_seeded(session):
        metadata = await sde_repo.get_metadata(session)
        if metadata is not None and (metadata.seed_version or 0) >= SEED_VERSION:
            return None
    return await seed_reference_data(session, source, source_label=source_label)
