"""Queries and bulk-seed writes for the SDE reference tables (ADR-0009).

Reads return Pydantic records; the bulk upserts return a row count (a scalar, not
an ORM entity). The unit of work — `commit()` — is owned by the application layer.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import (
    SdeMarketGroup,
    SdeMetadata,
    SdeStation,
    SdeType,
    SdeTypeMaterial,
)
from app.data.records import (
    SdeMarketGroupRecord,
    SdeMetadataRecord,
    SdeStationRecord,
    SdeTypeRecord,
)

# SQLite caps a statement at 999 bound parameters; keep batches well under that.
_BATCH = 500


async def get_type(session: AsyncSession, type_id: int) -> SdeTypeRecord | None:
    row = await session.get(SdeType, type_id)
    return SdeTypeRecord.model_validate(row) if row is not None else None


async def get_types(
    session: AsyncSession, type_ids: Sequence[int]
) -> dict[int, SdeTypeRecord]:
    """Batch lookup keyed by type_id (missing ids are simply absent)."""
    if not type_ids:
        return {}
    stmt = select(SdeType).where(SdeType.type_id.in_(type_ids))
    rows = (await session.execute(stmt)).scalars().all()
    return {r.type_id: SdeTypeRecord.model_validate(r) for r in rows}


async def get_types_by_names(
    session: AsyncSession, names: Sequence[str]
) -> dict[str, list[SdeTypeRecord]]:
    """Exact, case-insensitive name lookup, keyed by lowercased name (for paste
    resolution). Returns **all** matches per name — EVE SDE has duplicate type
    names, so a name can map to more than one type. The caller decides how to
    handle ambiguity (resolve a single match, reject otherwise). Unmatched names
    are simply absent."""
    if not names:
        return {}
    lowered = {n.lower() for n in names}
    stmt = select(SdeType).where(func.lower(SdeType.name).in_(lowered)).order_by(
        SdeType.type_id
    )
    rows = (await session.execute(stmt)).scalars().all()
    by_name: dict[str, list[SdeTypeRecord]] = {}
    for r in rows:
        by_name.setdefault(r.name.lower(), []).append(
            SdeTypeRecord.model_validate(r)
        )
    return by_name


async def get_market_group(
    session: AsyncSession, market_group_id: int
) -> SdeMarketGroupRecord | None:
    row = await session.get(SdeMarketGroup, market_group_id)
    return SdeMarketGroupRecord.model_validate(row) if row is not None else None


async def search_types(
    session: AsyncSession, query: str, limit: int
) -> list[SdeTypeRecord]:
    """Case-insensitive substring match on the type name, ordered by name."""
    pattern = f"%{query.lower()}%"
    stmt = (
        select(SdeType)
        .where(func.lower(SdeType.name).like(pattern))
        .order_by(SdeType.name)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [SdeTypeRecord.model_validate(r) for r in rows]


async def list_market_groups(session: AsyncSession) -> list[SdeMarketGroupRecord]:
    stmt = select(SdeMarketGroup).order_by(SdeMarketGroup.market_group_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [SdeMarketGroupRecord.model_validate(r) for r in rows]


async def get_station(
    session: AsyncSession, station_id: int
) -> SdeStationRecord | None:
    row = await session.get(SdeStation, station_id)
    return SdeStationRecord.model_validate(row) if row is not None else None


async def search_stations(
    session: AsyncSession, query: str, limit: int
) -> list[SdeStationRecord]:
    """Case-insensitive substring match on the system *or* station name, so typing a
    system ("Korsiki") or a station name both find it. Ordered by system then name."""
    pattern = f"%{query.lower()}%"
    stmt = (
        select(SdeStation)
        .where(
            func.lower(SdeStation.system_name).like(pattern)
            | func.lower(SdeStation.name).like(pattern)
        )
        .order_by(SdeStation.system_name, SdeStation.name)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [SdeStationRecord.model_validate(r) for r in rows]


async def bulk_upsert_stations(
    session: AsyncSession, rows: Sequence[dict]
) -> int:
    """Insert-or-update SdeStation rows keyed by `station_id`. Returns the row count."""
    for start in range(0, len(rows), _BATCH):
        batch = rows[start : start + _BATCH]
        stmt = pg_insert(SdeStation).values(list(batch))
        stmt = stmt.on_conflict_do_update(
            index_elements=[SdeStation.station_id],
            set_={
                "name": stmt.excluded.name,
                "system_name": stmt.excluded.system_name,
                "region_id": stmt.excluded.region_id,
            },
        )
        await session.execute(stmt)
    return len(rows)


async def bulk_upsert_types(
    session: AsyncSession, rows: Sequence[dict]
) -> int:
    """Insert-or-update SdeType rows keyed by `type_id`. Returns the row count."""
    for start in range(0, len(rows), _BATCH):
        batch = rows[start : start + _BATCH]
        stmt = pg_insert(SdeType).values(list(batch))
        stmt = stmt.on_conflict_do_update(
            index_elements=[SdeType.type_id],
            set_={
                "name": stmt.excluded.name,
                "group_id": stmt.excluded.group_id,
                "category_id": stmt.excluded.category_id,
                "market_group_id": stmt.excluded.market_group_id,
                "volume": stmt.excluded.volume,
                "portion_size": stmt.excluded.portion_size,
                "published": stmt.excluded.published,
            },
        )
        await session.execute(stmt)
    return len(rows)


async def bulk_upsert_type_materials(
    session: AsyncSession, rows: Sequence[dict]
) -> int:
    """Insert-or-update reprocessing yields, keyed by `(type_id, material_type_id)`."""
    for start in range(0, len(rows), _BATCH):
        batch = rows[start : start + _BATCH]
        stmt = pg_insert(SdeTypeMaterial).values(list(batch))
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                SdeTypeMaterial.type_id,
                SdeTypeMaterial.material_type_id,
            ],
            set_={"quantity": stmt.excluded.quantity},
        )
        await session.execute(stmt)
    return len(rows)


async def get_type_materials(
    session: AsyncSession, type_ids: Sequence[int]
) -> dict[int, list[tuple[int, int]]]:
    """Reprocessing yields for the given types, keyed by type_id → list of
    `(material_type_id, quantity)`. Types with no rows are simply absent."""
    if not type_ids:
        return {}
    stmt = select(SdeTypeMaterial).where(SdeTypeMaterial.type_id.in_(type_ids))
    rows = (await session.execute(stmt)).scalars().all()
    result: dict[int, list[tuple[int, int]]] = {}
    for r in rows:
        result.setdefault(r.type_id, []).append((r.material_type_id, r.quantity))
    return result


async def bulk_upsert_market_groups(
    session: AsyncSession, rows: Sequence[dict]
) -> int:
    """Insert-or-update SdeMarketGroup rows keyed by `market_group_id`."""
    for start in range(0, len(rows), _BATCH):
        batch = rows[start : start + _BATCH]
        stmt = pg_insert(SdeMarketGroup).values(list(batch))
        stmt = stmt.on_conflict_do_update(
            index_elements=[SdeMarketGroup.market_group_id],
            set_={
                "parent_id": stmt.excluded.parent_id,
                "name": stmt.excluded.name,
            },
        )
        await session.execute(stmt)
    return len(rows)


async def get_metadata(session: AsyncSession) -> SdeMetadataRecord | None:
    row = await session.get(SdeMetadata, 1)
    return SdeMetadataRecord.model_validate(row) if row is not None else None


async def set_metadata(
    session: AsyncSession,
    *,
    source: str,
    type_count: int,
    market_group_count: int,
    imported_at: datetime,
) -> SdeMetadataRecord:
    stmt = pg_insert(SdeMetadata).values(
        id=1,
        source=source,
        type_count=type_count,
        market_group_count=market_group_count,
        imported_at=imported_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SdeMetadata.id],
        set_={
            "source": stmt.excluded.source,
            "type_count": stmt.excluded.type_count,
            "market_group_count": stmt.excluded.market_group_count,
            "imported_at": stmt.excluded.imported_at,
        },
    )
    await session.execute(stmt)
    return SdeMetadataRecord(
        source=source,
        type_count=type_count,
        market_group_count=market_group_count,
        imported_at=imported_at,
    )
