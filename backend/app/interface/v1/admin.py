"""App-admin endpoints (ADR-0041): the instance operator's surface, and the app's one
deliberately cross-tenant router. Every endpoint requires `require_app_admin`; nothing
here is corp-scoped, and nothing corp-scoped belongs here.

Feature access (ADR-0042) is managed per corp by EVE corporation id. The only gated
feature today is the accounting add-on, so the feature key is fixed server-side."""

from fastapi import APIRouter, status

from app.application import entitlements as entitlements_app
from app.interface.deps import SessionDep
from app.interface.security import RequireAppAdmin
from app.schemas.admin import AccessGrantRequest, CorpAccessOut

router = APIRouter(prefix="/admin", tags=["admin"])

# The single gated feature (ADR-0042). New paid features widen the domain Literal and
# surface their own key here.
_FEATURE = "accounting"


@router.get("/access", response_model=list[CorpAccessOut])
async def list_corp_access(
    user: RequireAppAdmin, session: SessionDep
) -> list[CorpAccessOut]:
    """All registered corps with their accounting-access status (cross-tenant)."""
    access = await entitlements_app.list_corp_access(session, feature=_FEATURE)
    return [CorpAccessOut(**a.model_dump()) for a in access]


@router.put("/access/{corporation_id}", response_model=CorpAccessOut)
async def grant_corp_access(
    corporation_id: int,
    payload: AccessGrantRequest,
    user: RequireAppAdmin,
    session: SessionDep,
) -> CorpAccessOut:
    """Grant or extend a corp's access (`source=admin`); null expiry = perpetual."""
    access = await entitlements_app.grant_access(
        session,
        corporation_eve_id=corporation_id,
        feature=_FEATURE,
        expires_at=payload.expires_at,
        granted_by_character_id=user.character_id,
    )
    return CorpAccessOut(**access.model_dump())


@router.delete("/access/{corporation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_corp_access(
    corporation_id: int, user: RequireAppAdmin, session: SessionDep
) -> None:
    """Revoke a corp's access. Idempotent."""
    await entitlements_app.revoke_access(
        session, corporation_eve_id=corporation_id, feature=_FEATURE
    )
