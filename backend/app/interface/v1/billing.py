"""Accounting-access checkout view (ADR-0042): what a corp's manager sees to pay —
their access status, the price, their payment reference, and where the ISK goes.
Corp-scoped and manager-gated; the admin's cross-tenant tools live in admin.py."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.application import payments as payments_app
from app.application.auth import AuthenticatedUser
from app.interface.deps import SessionDep
from app.interface.security import require_role
from app.schemas.billing import AccountingAccessOut

router = APIRouter(prefix="/corporations/me", tags=["billing"])

ManagerUser = Annotated[AuthenticatedUser, Depends(require_role("manager"))]


@router.get("/accounting-access", response_model=AccountingAccessOut)
async def get_accounting_access(
    user: ManagerUser, session: SessionDep
) -> AccountingAccessOut:
    info = await payments_app.checkout_info(
        session, corporation_eve_id=user.corporation_id
    )
    return AccountingAccessOut(**info.model_dump())
