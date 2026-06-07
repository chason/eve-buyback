"""Queries and bulk-seed writes for the SDE reference tables (ADR-0009).

Reads return Pydantic records; the bulk upserts return a row count (a scalar, not
an ORM entity). The unit of work — `commit()` — is owned by the application layer.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import SdeMarketGroup, SdeMetadata, SdeType
from app.data.records import (
    SdeMarketGroupRecord,
    SdeMetadataRecord,
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
) -> dict[str, SdeTypeRecord]:
    """Exact, case-insensitive name lookup, keyed by lowercased name (for paste
    resolution). Unmatched names are simply absent."""
    if not names:
        return {}
    lowered = {n.lower() for n in names}
    stmt = select(SdeType).where(func.lower(SdeType.name).in_(lowered))
    rows = (await session.execute(stmt)).scalars().all()
    return {r.name.lower(): SdeTypeRecord.model_validate(r) for r in rows}


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
                "market_group_id": stmt.excluded.market_group_id,
                "volume": stmt.excluded.volume,
                "published": stmt.excluded.published,
            },
        )
        await session.execute(stmt)
    return len(rows)


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
