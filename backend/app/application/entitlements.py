"""Entitlement gate (ADR-0042): whether a corp holds an active grant for a paid
feature, and the check every gated use case runs first.

The gate is enforced HERE, in the application layer — hiding a page in the SPA is
cosmetic; a use case that skips `require_entitlement` is ungated. Granting/revoking
(the app-admin actions, ADR-0041) are separate use cases added with the admin surface.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import EntitlementRequired
from app.data.repositories import entitlements as entitlements_repo
from app.domain.entitlements import Feature, entitlement_active


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
