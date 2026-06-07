from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.application import appraisals as appraisals_app
from app.application.appraisals import AppraisalItem
from app.interface.deps import SessionDep
from app.interface.security import RequireUser
from app.plugins.fuzzwork import FuzzworkClient, get_fuzzwork_client
from app.schemas.appraisal import (
    AppraisalCreateRequest,
    AppraisalOut,
    AppraisalSummaryOut,
)

router = APIRouter(prefix="/appraisals", tags=["appraisals"])

FuzzworkDep = Annotated[FuzzworkClient, Depends(get_fuzzwork_client)]


@router.post("", response_model=AppraisalOut, status_code=status.HTTP_201_CREATED)
async def create_appraisal(
    payload: AppraisalCreateRequest,
    user: RequireUser,
    session: SessionDep,
    fuzzwork: FuzzworkDep,
) -> AppraisalOut:
    items = [
        AppraisalItem(type_id=i.type_id, quantity=i.quantity) for i in payload.items
    ]
    record = await appraisals_app.create_appraisal(
        session,
        fuzzwork,
        user=user,
        items=items,
        paste=payload.paste,
        now=datetime.now(UTC),
    )
    return AppraisalOut(**record.model_dump())


@router.get("", response_model=list[AppraisalSummaryOut])
async def list_appraisals(
    user: RequireUser, session: SessionDep
) -> list[AppraisalSummaryOut]:
    records = await appraisals_app.list_appraisals(session, user=user)
    return [AppraisalSummaryOut(**r.model_dump()) for r in records]


@router.get("/{public_id}", response_model=AppraisalOut)
async def get_appraisal(
    public_id: str, user: RequireUser, session: SessionDep
) -> AppraisalOut:
    record = await appraisals_app.get_appraisal(
        session, corporation_id=user.corporation_id, public_id=public_id
    )
    return AppraisalOut(**record.model_dump())
