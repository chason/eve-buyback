"""Structure-market token persistence (ADR-0029). One encrypted refresh token per
corporation. Returns records (incl. the ciphertext for internal refresh use); the
interface maps to a status DTO that omits it. The application owns the commit."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import CorpEsiToken, Corporation
from app.data.records import CorpEsiTokenRecord


async def get_for_corp(
    session: AsyncSession, corporation_id: uuid.UUID
) -> CorpEsiTokenRecord | None:
    row = await _row(session, corporation_id)
    return (
        CorpEsiTokenRecord.model_validate(row) if row is not None else None
    )


async def list_corps_by_token_health(
    session: AsyncSession, corporation_ids: Sequence[uuid.UUID]
) -> list[uuid.UUID]:
    """The corp ids (among `corporation_ids`) that hold a structure token, ordered
    healthiest-first for the background refresh's token selection (ADR-0034): tokens
    not flagged `last_refresh_failed_at` come before flagged ones, then
    least-recently-authorized first so the job doesn't always lean on the newest grant.
    Corps without a token are absent. The ordering is done in SQL, not Python."""
    if not corporation_ids:
        return []
    stmt = (
        select(CorpEsiToken.corporation_id)
        .where(CorpEsiToken.corporation_id.in_(corporation_ids))
        .order_by(
            # False (healthy) sorts before True (failed);
            CorpEsiToken.last_refresh_failed_at.is_not(None),
            # then least-recently-used first (never-used first), so the fetching token
            # rotates across corps each cycle (ADR-0034, #88);
            CorpEsiToken.last_used_at.asc().nulls_first(),
            # then oldest grant as a stable tiebreak among the never-used.
            CorpEsiToken.created_at.asc(),
        )
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_corp_eve_ids_with_token(session: AsyncSession) -> list[int]:
    """EVE corp ids of every corporation holding a corp-ESI token (ADR-0036). Joined to
    `corporations` so the daily roster-refresh job can drive the EVE-id-keyed use case."""
    stmt = select(Corporation.eve_id).join(
        CorpEsiToken, CorpEsiToken.corporation_id == Corporation.id
    )
    return list((await session.execute(stmt)).scalars().all())


async def upsert_token(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    character_id: uuid.UUID,
    character_eve_id: int,
    character_name: str,
    encrypted_refresh_token: bytes,
    scopes: str,
) -> CorpEsiTokenRecord:
    """Create or replace the corp's structure-market authorization."""
    row = await _row(session, corporation_id)
    if row is None:
        row = CorpEsiToken(corporation_id=corporation_id)
        session.add(row)
    row.character_id = character_id
    row.character_eve_id = character_eve_id
    row.character_name = character_name
    row.encrypted_refresh_token = encrypted_refresh_token
    row.scopes = scopes
    row.last_refresh_failed_at = None  # a fresh authorization clears any failure
    await session.flush()
    await session.refresh(row)
    return CorpEsiTokenRecord.model_validate(row)


async def update_refresh_token(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    encrypted_refresh_token: bytes,
) -> None:
    """Persist a rotated refresh token (EVE may rotate it on refresh)."""
    await session.execute(
        update(CorpEsiToken)
        .where(CorpEsiToken.corporation_id == corporation_id)
        .values(encrypted_refresh_token=encrypted_refresh_token)
    )


async def mark_failed(
    session: AsyncSession, *, corporation_id: uuid.UUID, at: datetime
) -> None:
    """Record that a refresh failed (revoked grant / lost docking access)."""
    await session.execute(
        update(CorpEsiToken)
        .where(CorpEsiToken.corporation_id == corporation_id)
        .values(last_refresh_failed_at=at)
    )


async def mark_used(
    session: AsyncSession, *, corporation_id: uuid.UUID, at: datetime
) -> None:
    """Record that this corp's token was just used to fetch a structure book (ADR-0034
    rotation, #88), so least-recently-used selection moves to another corp next cycle.
    A successful fetch also **clears** any prior failure flag — access is healthy again
    (#68), so the manager's "re-authorize" warning self-heals without a re-auth."""
    await session.execute(
        update(CorpEsiToken)
        .where(CorpEsiToken.corporation_id == corporation_id)
        .values(last_used_at=at, last_refresh_failed_at=None)
    )


async def delete_for_corp(
    session: AsyncSession, corporation_id: uuid.UUID
) -> bool:
    """Revoke (delete) the corp's authorization. Returns whether a row was removed."""
    result = await session.execute(
        delete(CorpEsiToken).where(
            CorpEsiToken.corporation_id == corporation_id
        )
    )
    return result.rowcount > 0


async def _row(
    session: AsyncSession, corporation_id: uuid.UUID
) -> CorpEsiToken | None:
    stmt = select(CorpEsiToken).where(
        CorpEsiToken.corporation_id == corporation_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()
