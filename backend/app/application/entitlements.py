"""Entitlement use cases (ADR-0042): the gate every paid use case runs first, plus
the app-admin actions (ADR-0041) — list all corps' access, grant/extend, revoke.

The gate is enforced HERE, in the application layer — hiding a page in the SPA is
cosmetic; a use case that skips `require_entitlement` is ungated.
"""

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import (
    CorporationNotRegistered,
    EntitlementRequired,
    GrantExpiryInPast,
)
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.domain.entitlements import EntitlementSource, Feature, entitlement_active


class CorpFeatureAccess(BaseModel):
    """One corp's access to a feature, as the admin surface sees it: the stored
    entitlement facts plus the derived `active` flag."""

    corporation_id: int  # EVE id
    corporation_name: str
    active: bool
    source: EntitlementSource | None = None
    granted_at: datetime | None = None
    expires_at: datetime | None = None
    granted_by_character_id: int | None = None


async def corp_has_entitlement(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    feature: Feature,
    now: datetime | None = None,
) -> bool:
    """Whether the corp holds an active entitlement for `feature` (ADR-0042)."""
    record = await entitlements_repo.get(
        session, corporation_id=corporation_id, feature=feature
    )
    if record is None:
        return False
    return entitlement_active(record.expires_at, now or datetime.now(UTC))


async def require_entitlement(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    feature: Feature,
    now: datetime | None = None,
) -> None:
    """Raise `EntitlementRequired` unless the corp's entitlement is active. Every
    gated use case calls this first."""
    if not await corp_has_entitlement(
        session, corporation_id=corporation_id, feature=feature, now=now
    ):
        raise EntitlementRequired()


# --- app-admin actions (ADR-0041) ------------------------------------------------
# Callers are gated by `require_app_admin` at the interface; these use cases own the
# unit of work. All are keyed by the corp's EVE id (the admin API is EVE-keyed).


async def list_corp_access(
    session: AsyncSession, *, feature: Feature, now: datetime | None = None
) -> list[CorpFeatureAccess]:
    """Every registered corp with its access status for `feature` — the admin list."""
    now = now or datetime.now(UTC)
    records = await entitlements_repo.list_corporations_with_feature(
        session, feature=feature
    )
    return [
        CorpFeatureAccess(
            active=(r.granted_at is not None and entitlement_active(r.expires_at, now)),
            **r.model_dump(),
        )
        for r in records
    ]


async def grant_access(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    feature: Feature,
    expires_at: datetime | None,
    granted_by_character_id: int,
    now: datetime | None = None,
) -> CorpFeatureAccess:
    """Grant or extend a corp's access by admin action (`source=admin`, ADR-0042).
    `expires_at` None = perpetual. Re-granting rewrites expiry/source in place. A
    dated grant must end in the future — granting already-lapsed access is always a
    mistake (the UI enforces this too; this is the authoritative check)."""
    now = now or datetime.now(UTC)
    if expires_at is not None and expires_at <= now:
        raise GrantExpiryInPast()
    corp = await corporations_repo.get_by_eve_id(session, corporation_eve_id)
    if corp is None:
        raise CorporationNotRegistered()
    record = await entitlements_repo.upsert(
        session,
        corporation_id=corp.id,
        feature=feature,
        source="admin",
        expires_at=expires_at,
        granted_by_character_id=granted_by_character_id,
    )
    await session.commit()
    return CorpFeatureAccess(
        corporation_id=corp.corporation_id,
        corporation_name=corp.name,
        active=entitlement_active(record.expires_at, now or datetime.now(UTC)),
        source=record.source,
        granted_at=record.granted_at,
        expires_at=record.expires_at,
        granted_by_character_id=record.granted_by_character_id,
    )


async def revoke_access(
    session: AsyncSession, *, corporation_eve_id: int, feature: Feature
) -> None:
    """Revoke a corp's access. Idempotent: revoking a corp with no grant is a no-op
    (the end state — no access — already holds)."""
    corp = await corporations_repo.get_by_eve_id(session, corporation_eve_id)
    if corp is None:
        raise CorporationNotRegistered()
    await entitlements_repo.delete(session, corporation_id=corp.id, feature=feature)
    await session.commit()
