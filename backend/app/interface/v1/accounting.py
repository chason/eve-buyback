"""Accounting add-on endpoints (ADR-0043). Manager-gated at the interface; the
entitlement gate (ADR-0042) is enforced in the application layer and surfaces here
as 402 via the error mapping."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.application import hangar as hangar_app
from app.application import lots as lots_app
from app.application import reconciliation as reconciliation_app
from app.application import transformations as transformations_app
from app.application.auth import AuthenticatedUser
from app.config import get_settings
from app.interface.deps import SessionDep
from app.interface.security import require_role
from app.plugins.esi import EsiClient, get_esi_client
from app.plugins.sso import EveSsoClient, get_sso_client
from app.plugins.token_cipher import TokenCipher, get_token_cipher
from app.schemas.accounting import (
    HangarCheckResult,
    HangarCreateRequest,
    HangarOut,
    InventoryOut,
    ReconciliationEventOut,
    ReprocessOutputOut,
    ReprocessPreviewOut,
    ReprocessRequest,
    ReprocessResultOut,
)

router = APIRouter(prefix="/corporations/me/accounting", tags=["accounting"])

ManagerUser = Annotated[AuthenticatedUser, Depends(require_role("manager"))]
SsoDep = Annotated[EveSsoClient, Depends(get_sso_client)]
EsiDep = Annotated[EsiClient, Depends(get_esi_client)]
CipherDep = Annotated[TokenCipher, Depends(get_token_cipher)]


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


@router.get("/reconciliation", response_model=list[ReconciliationEventOut])
async def list_reconciliation_events(
    user: ManagerUser, session: SessionDep
) -> list[ReconciliationEventOut]:
    views = await reconciliation_app.list_events(
        session, corporation_eve_id=user.corporation_id
    )
    return [
        ReconciliationEventOut(
            kind=v.record.kind,
            type_id=v.record.type_id,
            type_name=v.type_name,
            location_id=v.record.location_id,
            location_name=v.location_name,
            qty=v.record.qty,
            unit_cost=v.record.unit_cost,
            booked=v.record.lot_id is not None,
            flagged=v.record.flagged,
            note=v.record.note,
            occurred_at=v.record.occurred_at,
        )
        for v in views
    ]


@router.get(
    "/lots/{lot_id}/reprocess-preview", response_model=ReprocessPreviewOut
)
async def reprocess_preview(
    lot_id: uuid.UUID, user: ManagerUser, session: SessionDep
) -> ReprocessPreviewOut:
    preview = await transformations_app.preview_reprocess(
        session, corporation_eve_id=user.corporation_id, lot_id=lot_id
    )
    return ReprocessPreviewOut(
        lot_id=preview.lot.id,
        type_id=preview.lot.item_type_id,
        type_name=preview.source_type_name,
        qty_remaining=preview.lot.qty_remaining,
        outputs=[
            ReprocessOutputOut(type_id=tid, type_name=name, quantity=qty)
            for tid, name, qty in preview.outputs
        ],
    )


@router.post("/lots/{lot_id}/reprocess", response_model=ReprocessResultOut)
async def record_reprocess(
    lot_id: uuid.UUID,
    payload: ReprocessRequest,
    user: ManagerUser,
    session: SessionDep,
) -> ReprocessResultOut:
    children = await transformations_app.record_reprocess(
        session,
        corporation_eve_id=user.corporation_id,
        lot_id=lot_id,
        qty=payload.qty,
        outputs={o.type_id: o.quantity for o in payload.outputs},
        recorded_by_character_id=user.character_id,
    )
    return ReprocessResultOut(
        children=[
            ReprocessOutputOut(type_id=c.item_type_id, quantity=c.qty_original)
            for c in children
        ]
    )


@router.post("/hangar-check", response_model=HangarCheckResult)
async def run_hangar_check(
    user: ManagerUser,
    session: SessionDep,
    sso: SsoDep,
    esi: EsiDep,
    cipher: CipherDep,
) -> HangarCheckResult:
    """The "Check the hangar now" button. Safe to click freely: ESI caches corp
    assets for an hour, and the pass is idempotent on the delta (ADR-0044)."""
    result = await reconciliation_app.run_manual_check(
        session,
        sso,
        esi,
        corporation_eve_id=user.corporation_id,
        cipher=cipher,
        excess_flag_isk=get_settings().accounting_excess_flag_isk,
    )
    return HangarCheckResult(**result.model_dump())
