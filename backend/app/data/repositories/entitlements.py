"""Entitlement persistence (ADR-0042): one row per (corp, feature), updated in place
on grant/extend. Repositories return records and never commit (data/ conventions)."""

import uuid
from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Corporation, Entitlement
from app.data.records import CorpFeatureAccessRecord, EntitlementRecord
from app.domain.entitlements import EntitlementSource, Feature


async def get(
    session: AsyncSession, *, corporation_id: uuid.UUID, feature: Feature
) -> EntitlementRecord | None:
    row = await _get_row(session, corporation_id=corporation_id, feature=feature)
    return EntitlementRecord.model_validate(row) if row else None


async def upsert(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    feature: Feature,
    source: EntitlementSource,
    expires_at: datetime | None,
    granted_by_character_id: int | None = None,
) -> EntitlementRecord:
    """Grant or update the corp's entitlement: one row per (corp, feature), so a
    re-grant/extension rewrites expiry, source, and grantor in place (`granted_at`
    keeps the original grant time)."""
    row = await _get_row(session, corporation_id=corporation_id, feature=feature)
    if row is None:
        row = Entitlement(
            corporation_id=corporation_id,
            feature=feature,
            source=source,
            expires_at=expires_at,
            granted_by_character_id=granted_by_character_id,
        )
        session.add(row)
    else:
        row.source = source
        row.expires_at = expires_at
        row.granted_by_character_id = granted_by_character_id
    await session.flush()  # populate server defaults (granted_at)
    await session.refresh(row)
    return EntitlementRecord.model_validate(row)


async def delete(
    session: AsyncSession, *, corporation_id: uuid.UUID, feature: Feature
) -> bool:
    """Revoke the entitlement; return False if none existed."""
    row = await _get_row(session, corporation_id=corporation_id, feature=feature)
    if row is None:
        return False
    await session.delete(row)
    return True


async def list_corporations_with_feature(
    session: AsyncSession, *, feature: Feature
) -> list[CorpFeatureAccessRecord]:
    """Every registered corp with its entitlement facts for `feature` (LEFT JOIN — corps
    without a row appear with None fields). The app-admin list (ADR-0041/0042): the one
    deliberately cross-tenant read, so it lives here and nowhere else."""
    stmt = (
        select(Corporation, Entitlement)
        .join(
            Entitlement,
            and_(
                Entitlement.corporation_id == Corporation.id,
                Entitlement.feature == feature,
            ),
            isouter=True,
        )
        .order_by(Corporation.name)
    )
    rows = (await session.execute(stmt)).all()
    return [
        CorpFeatureAccessRecord(
            corporation_id=corp.eve_id,
            corporation_name=corp.name,
            source=ent.source if ent else None,
            granted_at=ent.granted_at if ent else None,
            expires_at=ent.expires_at if ent else None,
            granted_by_character_id=ent.granted_by_character_id if ent else None,
        )
        for corp, ent in rows
    ]


async def _get_row(
    session: AsyncSession, *, corporation_id: uuid.UUID, feature: Feature
) -> Entitlement | None:
    result = await session.execute(
        select(Entitlement).where(
            Entitlement.corporation_id == corporation_id,
            Entitlement.feature == feature,
        )
    )
    return result.scalar_one_or_none()
