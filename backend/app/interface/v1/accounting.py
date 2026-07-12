"""Accounting add-on endpoints (ADR-0043). Manager-gated at the interface; the
entitlement gate (ADR-0042) is enforced in the application layer and surfaces here
as 402 via the error mapping."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.application import hangar as hangar_app
from app.application import lots as lots_app
from app.application.auth import AuthenticatedUser
from app.config import get_settings
from app.interface.deps import SessionDep
from app.interface.security import require_role
from app.schemas.accounting import HangarCreateRequest, HangarOut, InventoryOut

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


@router.get("/hangars", response_model=list[HangarOut])
async def list_hangars(user: ManagerUser, session: SessionDep) -> list[HangarOut]:
    records = await hangar_app.list_hangars(
        session, corporation_eve_id=user.corporation_id
    )
    return [HangarOut.model_validate(r.model_dump()) for r in records]


@router.post(
    "/hangars", response_model=HangarOut, status_code=status.HTTP_201_CREATED
)
async def add_hangar(
    payload: HangarCreateRequest, user: ManagerUser, session: SessionDep
) -> HangarOut:
    record = await hangar_app.add_hangar(
        session,
        corporation_eve_id=user.corporation_id,
        location_id=payload.location_id,
        division=payload.division,
    )
    return HangarOut.model_validate(record.model_dump())


@router.delete(
    "/hangars/{location_id}/{division}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_hangar(
    location_id: str, division: int, user: ManagerUser, session: SessionDep
) -> None:
    await hangar_app.remove_hangar(
        session,
        corporation_eve_id=user.corporation_id,
        location_id=location_id,
        division=division,
    )
