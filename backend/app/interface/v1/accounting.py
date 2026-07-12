"""Accounting add-on endpoints (ADR-0043). Manager-gated at the interface; the
entitlement gate (ADR-0042) is enforced in the application layer and surfaces here
as 402 via the error mapping."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.application import lots as lots_app
from app.application.auth import AuthenticatedUser
from app.config import get_settings
from app.interface.deps import SessionDep
from app.interface.security import require_role
from app.schemas.accounting import InventoryOut

router = APIRouter(prefix="/corporations/me/accounting", tags=["accounting"])

ManagerUser = Annotated[AuthenticatedUser, Depends(require_role("manager"))]


@router.get("/inventory", response_model=InventoryOut)
async def get_inventory(user: ManagerUser, session: SessionDep) -> InventoryOut:
    settings = get_settings()
    view = await lots_app.get_inventory(
        session,
        corporation_eve_id=user.corporation_id,
        stale_days=settings.accounting_stale_days,
        sales_tax_rate=settings.accounting_sales_tax_rate,
    )
    return InventoryOut.model_validate(view.model_dump())
