"""Structure-market token persistence (ADR-0029). One encrypted refresh token per
corporation. Returns records (incl. the ciphertext for internal refresh use); the
interface maps to a status DTO that omits it. The application owns the commit."""

import uuid
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import StructureMarketToken
from app.data.records import StructureMarketTokenRecord


async def get_for_corp(
    session: AsyncSession, corporation_id: uuid.UUID
) -> StructureMarketTokenRecord | None:
    row = await _row(session, corporation_id)
    return (
        StructureMarketTokenRecord.model_validate(row) if row is not None else None
    )


async def upsert_token(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    character_id: uuid.UUID,
    character_eve_id: int,
    character_name: str,
    encrypted_refresh_token: bytes,
    scopes: str,
) -> StructureMarketTokenRecord:
    """Create or replace the corp's structure-market authorization."""
    row = await _row(session, corporation_id)
    if row is None:
        row = StructureMarketToken(corporation_id=corporation_id)
        session.add(row)
    row.character_id = character_id
    row.character_eve_id = character_eve_id
    row.character_name = character_name
    row.encrypted_refresh_token = encrypted_refresh_token
    row.scopes = scopes
    row.last_refresh_failed_at = None  # a fresh authorization clears any failure
    await session.flush()
    await session.refresh(row)
    return StructureMarketTokenRecord.model_validate(row)


async def update_refresh_token(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    encrypted_refresh_token: bytes,
) -> None:
    """Persist a rotated refresh token (EVE may rotate it on refresh)."""
    await session.execute(
        update(StructureMarketToken)
        .where(StructureMarketToken.corporation_id == corporation_id)
        .values(encrypted_refresh_token=encrypted_refresh_token)
    )


async def mark_failed(
    session: AsyncSession, *, corporation_id: uuid.UUID, at: datetime
) -> None:
    """Record that a refresh failed (revoked grant / lost docking access)."""
    await session.execute(
        update(StructureMarketToken)
        .where(StructureMarketToken.corporation_id == corporation_id)
        .values(last_refresh_failed_at=at)
    )


async def delete_for_corp(
    session: AsyncSession, corporation_id: uuid.UUID
) -> bool:
    """Revoke (delete) the corp's authorization. Returns whether a row was removed."""
    result = await session.execute(
        delete(StructureMarketToken).where(
            StructureMarketToken.corporation_id == corporation_id
        )
    )
    return result.rowcount > 0


async def _row(
    session: AsyncSession, corporation_id: uuid.UUID
) -> StructureMarketToken | None:
    stmt = select(StructureMarketToken).where(
        StructureMarketToken.corporation_id == corporation_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()
