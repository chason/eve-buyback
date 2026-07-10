"""Operator wallet token persistence (ADR-0042): at most one row per instance — the
operator's own wallet credential for payment reconciliation. Repositories return
records and never commit (data/ conventions)."""

from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import OperatorWalletToken
from app.data.records import OperatorWalletTokenRecord


async def get(session: AsyncSession) -> OperatorWalletTokenRecord | None:
    row = (await session.execute(select(OperatorWalletToken))).scalar_one_or_none()
    return OperatorWalletTokenRecord.model_validate(row) if row else None


async def replace(
    session: AsyncSession,
    *,
    character_eve_id: int,
    character_name: str,
    encrypted_refresh_token: bytes,
    scopes: str,
) -> OperatorWalletTokenRecord:
    """Store the operator credential, replacing any existing one (singleton)."""
    await session.execute(delete(OperatorWalletToken))
    row = OperatorWalletToken(
        character_eve_id=character_eve_id,
        character_name=character_name,
        encrypted_refresh_token=encrypted_refresh_token,
        scopes=scopes,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return OperatorWalletTokenRecord.model_validate(row)


async def delete_token(session: AsyncSession) -> bool:
    result = await session.execute(delete(OperatorWalletToken))
    return result.rowcount > 0


async def update_refresh_token(
    session: AsyncSession, *, encrypted_refresh_token: bytes
) -> None:
    """Persist a rotated refresh token (EVE may rotate on every refresh)."""
    await session.execute(
        update(OperatorWalletToken).values(
            encrypted_refresh_token=encrypted_refresh_token,
            last_refresh_failed_at=None,
        )
    )


async def mark_failed(session: AsyncSession, *, at: datetime) -> None:
    await session.execute(
        update(OperatorWalletToken).values(last_refresh_failed_at=at)
    )
