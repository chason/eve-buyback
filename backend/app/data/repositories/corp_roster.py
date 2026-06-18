"""Read/write access to the cached corp roster (ADR-0036).

The roster is a snapshot of a corporation's members (EVE id + name) pulled from ESI at
the last manager-roster sync, searched by the manager-designation picker. Reads return
Pydantic records; the application layer owns the clock (`synced_at`) and the `commit()`.
"""

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import CorpRosterMember
from app.data.records import CorpMemberRecord


async def replace_roster(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    members: dict[int, str],
    synced_at: datetime,
) -> None:
    """Replace a corp's cached roster wholesale: drop the old rows and insert the
    freshly-synced `{eve_id: name}` members stamped with `synced_at`. Caller commits."""
    await session.execute(
        delete(CorpRosterMember).where(
            CorpRosterMember.corporation_id == corporation_id
        )
    )
    session.add_all(
        [
            CorpRosterMember(
                corporation_id=corporation_id,
                character_eve_id=eve_id,
                name=name,
                synced_at=synced_at,
            )
            for eve_id, name in members.items()
        ]
    )


async def search_members(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    query: str,
    limit: int = 25,
) -> list[CorpMemberRecord]:
    """Case-insensitive name search over the cached roster, ordered by name and capped
    at `limit` (a typeahead, not a full dump)."""
    stmt = (
        select(CorpRosterMember.character_eve_id, CorpRosterMember.name)
        .where(
            CorpRosterMember.corporation_id == corporation_id,
            CorpRosterMember.name.ilike(f"%{query}%"),
        )
        .order_by(CorpRosterMember.name)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [CorpMemberRecord(character_id=eve_id, name=name) for eve_id, name in rows]


async def roster_status(
    session: AsyncSession, *, corporation_id: uuid.UUID
) -> tuple[datetime | None, int]:
    """`(last_synced_at, member_count)` for a corp's cached roster — `(None, 0)` when it
    has never been synced (all rows share one sync timestamp, so `max` dates the sync)."""
    stmt = select(func.max(CorpRosterMember.synced_at), func.count()).where(
        CorpRosterMember.corporation_id == corporation_id
    )
    synced_at, count = (await session.execute(stmt)).one()
    return synced_at, count or 0
