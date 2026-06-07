from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.application import pricing as pricing_app
from app.application.auth import AuthenticatedUser
from app.domain.pricing import TargetKind
from app.interface.deps import SessionDep
from app.interface.security import RequireUser, require_role
from app.schemas.pricing import (
    ConfigOut,
    ConfigUpdateRequest,
    RuleOut,
    RulePutRequest,
)

router = APIRouter(prefix="/corporations/me", tags=["pricing"])

ManagerUser = Annotated[AuthenticatedUser, Depends(require_role("manager"))]


@router.get("/config", response_model=ConfigOut)
async def get_config(user: RequireUser, session: SessionDep) -> ConfigOut:
    config = await pricing_app.get_config(session, user.corporation_id)
    return ConfigOut(corporation_id=user.corporation_id, **config.model_dump())


@router.put("/config", response_model=ConfigOut)
async def update_config(
    payload: ConfigUpdateRequest, user: ManagerUser, session: SessionDep
) -> ConfigOut:
    config = await pricing_app.update_config(
        session, user.corporation_id, **payload.model_dump()
    )
    return ConfigOut(corporation_id=user.corporation_id, **config.model_dump())


@router.get("/rules", response_model=list[RuleOut])
async def list_rules(user: RequireUser, session: SessionDep) -> list[RuleOut]:
    rules = await pricing_app.list_rules(session, user.corporation_id)
    return [RuleOut(**r.model_dump()) for r in rules]


@router.put("/rules/{target_kind}/{target_id}", response_model=RuleOut)
async def set_rule(
    target_kind: TargetKind,
    target_id: int,
    payload: RulePutRequest,
    user: ManagerUser,
    session: SessionDep,
    response: Response,
) -> RuleOut:
    """Create or replace the rule for a target (idempotent). 201 on create, 200 on
    replace."""
    rule, created = await pricing_app.set_rule(
        session,
        corporation_id=user.corporation_id,
        target_kind=target_kind,
        target_id=target_id,
        basis=payload.basis,
        percentage=payload.percentage,
        enabled=payload.enabled,
        reprocess=payload.reprocess,
        compressed_only=payload.compressed_only,
        accepted=payload.accepted,
    )
    response.status_code = (
        status.HTTP_201_CREATED if created else status.HTTP_200_OK
    )
    return RuleOut(**rule.model_dump())


@router.delete(
    "/rules/{target_kind}/{target_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_rule(
    target_kind: TargetKind,
    target_id: int,
    user: ManagerUser,
    session: SessionDep,
) -> None:
    await pricing_app.delete_rule(
        session,
        corporation_id=user.corporation_id,
        target_kind=target_kind,
        target_id=target_id,
    )
