"""Accounting add-on endpoints (ADR-0043). Manager-gated at the interface; the
entitlement gate (ADR-0042) is enforced in the application layer and surfaces here
as 402 via the error mapping."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.application import divisions as divisions_app
from app.application import hangar as hangar_app
from app.application import lots as lots_app
from app.application import manual_entries as manual_app
from app.application import moves as moves_app
from app.application import profit as profit_app
from app.application import reconciliation as reconciliation_app
from app.application import sales as sales_app
from app.application import transformations as transformations_app
from app.application.auth import AuthenticatedUser
from app.config import get_settings
from app.interface.deps import SessionDep
from app.interface.security import require_role
from app.plugins.esi import EsiClient, get_esi_client
from app.plugins.sso import EveSsoClient, get_sso_client
from app.plugins.token_cipher import TokenCipher, get_token_cipher
from app.schemas.accounting import (
    DivisionNameOut,
    DivisionNamesOut,
    HangarCheckResult,
    HangarCreateRequest,
    HangarOut,
    InventoryOut,
    ManualExpenseRequest,
    ManualLotRequest,
    ManualSaleRequest,
    ManualSaleResult,
    MoveSuggestionOut,
    ProfitOut,
    ReceivableClearRequest,
    ReceivableCreateRequest,
    ReceivableOut,
    ReconciliationEventOut,
    ReprocessOutputOut,
    ReprocessPreviewOut,
    ReprocessRequest,
    ReprocessResultOut,
    WalletDivisionOut,
    WalletDivisionUpdateRequest,
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


@router.get("/profit", response_model=ProfitOut)
async def get_profit(
    user: ManagerUser,
    session: SessionDep,
    since: datetime | None = None,
    until: datetime | None = None,
) -> ProfitOut:
    """The "How we're doing" view (#159): sales sold in [since, until) and the
    expenses incurred in it, folded. Both bounds optional (open-ended)."""
    view = await profit_app.get_profit(
        session,
        corporation_eve_id=user.corporation_id,
        since=since,
        until=until,
    )
    return ProfitOut.model_validate(view.model_dump())


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


@router.get("/divisions", response_model=DivisionNamesOut)
async def get_division_names(
    user: ManagerUser,
    session: SessionDep,
    sso: SsoDep,
    esi: EsiDep,
    cipher: CipherDep,
) -> DivisionNamesOut:
    """The corp's real division names for the wallet/hangar pickers (ADR-0048).
    Best-effort: empty lists when the corp token can't read them — the frontend
    falls back to generic labels."""
    names = await divisions_app.get_division_names(
        session, sso, esi, corporation_eve_id=user.corporation_id, cipher=cipher
    )
    return DivisionNamesOut(
        wallet=_division_names_out(names.wallet),
        hangar=_division_names_out(names.hangar),
    )


def _division_names_out(names: dict[int, str]) -> list[DivisionNameOut]:
    return [DivisionNameOut(division=d, name=n) for d, n in sorted(names.items())]


@router.get("/wallet-division", response_model=WalletDivisionOut)
async def get_wallet_division(
    user: ManagerUser, session: SessionDep
) -> WalletDivisionOut:
    division = await sales_app.get_wallet_division(
        session, corporation_eve_id=user.corporation_id
    )
    return WalletDivisionOut(division=division)


@router.put("/wallet-division", response_model=WalletDivisionOut)
async def set_wallet_division(
    payload: WalletDivisionUpdateRequest, user: ManagerUser, session: SessionDep
) -> WalletDivisionOut:
    division = await sales_app.set_wallet_division(
        session,
        corporation_eve_id=user.corporation_id,
        division=payload.division,
    )
    return WalletDivisionOut(division=division)


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


@router.get("/move-suggestions", response_model=list[MoveSuggestionOut])
async def list_move_suggestions(
    user: ManagerUser, session: SessionDep
) -> list[MoveSuggestionOut]:
    """The pending "looks like a move" cards (ADR-0049, #200)."""
    views = await reconciliation_app.list_move_suggestions(
        session, corporation_eve_id=user.corporation_id
    )
    return [_move_suggestion_out(v) for v in views]


@router.post(
    "/move-suggestions/{suggestion_id}/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def confirm_move_suggestion(
    suggestion_id: uuid.UUID, user: ManagerUser, session: SessionDep
) -> None:
    """"Yes, this was a move" (ADR-0049, #201): reverses the estimated-value
    stock at the destination and moves the origin's oldest matching stock there,
    cost basis and aging intact — atomically, logged with who confirmed."""
    await moves_app.confirm_move(
        session,
        corporation_eve_id=user.corporation_id,
        suggestion_id=suggestion_id,
        confirmed_by_character_id=user.character_id,
        confirmed_by_name=user.character_name,
    )


def _move_suggestion_out(
    v: reconciliation_app.MoveSuggestionView,
) -> MoveSuggestionOut:
    return MoveSuggestionOut(
        id=v.record.id,
        type_id=v.record.type_id,
        type_name=v.type_name,
        origin_location_id=v.record.origin_location_id,
        origin_name=v.origin_name,
        destination_location_id=v.record.destination_location_id,
        destination_name=v.destination_name,
        qty=v.record.qty,
        noticed_at=v.record.created_at,
    )


@router.post(
    "/move-suggestions/{suggestion_id}/dismiss",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def dismiss_move_suggestion(
    suggestion_id: uuid.UUID, user: ManagerUser, session: SessionDep
) -> None:
    """"Not a move" (ADR-0049, #202): the card goes away and won't come back
    while nothing changed; everything already booked stays as it is."""
    await reconciliation_app.dismiss_move_suggestion(
        session,
        corporation_eve_id=user.corporation_id,
        suggestion_id=suggestion_id,
        dismissed_by_character_name=user.character_name,
    )


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


@router.post("/manual/sale", response_model=ManualSaleResult)
async def record_manual_sale(
    payload: ManualSaleRequest, user: ManagerUser, session: SessionDep
) -> ManualSaleResult:
    missing = await manual_app.record_manual_sale(
        session,
        corporation_eve_id=user.corporation_id,
        type_id=payload.type_id,
        qty=payload.qty,
        unit_proceeds=payload.unit_proceeds,
        location_id=payload.location_id,
        note=payload.note,
        entered_by_character_id=user.character_id,
        sold_at=payload.sold_at,
    )
    return ManualSaleResult(stock_was_missing=missing)


@router.post("/manual/lot", status_code=status.HTTP_201_CREATED)
async def record_manual_lot(
    payload: ManualLotRequest, user: ManagerUser, session: SessionDep
) -> None:
    await manual_app.record_manual_lot(
        session,
        corporation_eve_id=user.corporation_id,
        type_id=payload.type_id,
        qty=payload.qty,
        unit_cost=payload.unit_cost,
        location_id=payload.location_id,
        note=payload.note,
        entered_by_character_id=user.character_id,
        cost_is_estimated=payload.cost_is_estimated,
        acquired_at=payload.acquired_at,
    )


@router.post("/manual/expense", status_code=status.HTTP_201_CREATED)
async def record_manual_expense(
    payload: ManualExpenseRequest, user: ManagerUser, session: SessionDep
) -> None:
    await manual_app.record_manual_expense(
        session,
        corporation_eve_id=user.corporation_id,
        kind=payload.kind,
        amount=payload.amount,
        note=payload.note,
        entered_by_character_id=user.character_id,
        incurred_at=payload.incurred_at,
    )


@router.get("/receivables", response_model=list[ReceivableOut])
async def list_receivables(
    user: ManagerUser, session: SessionDep
) -> list[ReceivableOut]:
    records = await manual_app.list_receivables(
        session, corporation_eve_id=user.corporation_id
    )
    return [ReceivableOut.model_validate(r.model_dump()) for r in records]


@router.post(
    "/receivables", response_model=ReceivableOut, status_code=status.HTTP_201_CREATED
)
async def create_receivable(
    payload: ReceivableCreateRequest, user: ManagerUser, session: SessionDep
) -> ReceivableOut:
    record = await manual_app.create_receivable(
        session,
        corporation_eve_id=user.corporation_id,
        amount=payload.amount,
        note=payload.note,
        entered_by_character_id=user.character_id,
    )
    return ReceivableOut.model_validate(record.model_dump())


@router.post("/receivables/{receivable_id}/clear", response_model=ReceivableOut)
async def clear_receivable(
    receivable_id: uuid.UUID,
    payload: ReceivableClearRequest,
    user: ManagerUser,
    session: SessionDep,
) -> ReceivableOut:
    record = await manual_app.clear_receivable(
        session,
        corporation_eve_id=user.corporation_id,
        receivable_id=receivable_id,
        cleared_by_character_id=user.character_id,
        cleared_note=payload.note,
    )
    return ReceivableOut.model_validate(record.model_dump())


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
