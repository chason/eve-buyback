from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.application import pricing as pricing_app
from app.application.auth import AuthenticatedUser
from app.interface.deps import SessionDep
from app.interface.security import RequireUser, require_role
from app.schemas.pricing import (
    ConfigOut,
    ConfigUpdateRequest,
    RuleCreateRequest,
    RuleOut,
    RuleUpdateRequest,
)

router = APIRouter(prefix="/corporations/me", tags=["pricing"])

ManagerUser = Annotated[AuthenticatedUser, Depends(require_role("manager"))]


@router.get("/config", response_model=ConfigOut)
async def get_config(user: RequireUser, session: SessionDep) -> ConfigOut:
    config = await pricing_app.get_config(session, user.corporation_id)
    return ConfigOut(**config.model_dump())


@router.put("/config", response_model=ConfigOut)
async def update_config(
    payload: ConfigUpdateRequest, user: ManagerUser, session: SessionDep
) -> ConfigOut:
    config = await pricing_app.update_config(
        session, user.corporation_id, **payload.model_dump()
    )
    return ConfigOut(**config.model_dump())


@router.get("/rules", response_model=list[RuleOut])
async def list_rules(user: RequireUser, session: SessionDep) -> list[RuleOut]:
    rules = await pricing_app.list_rules(session, user.corporation_id)
    return [RuleOut(**r.model_dump()) for r in rules]


@router.post("/rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: RuleCreateRequest, user: ManagerUser, session: SessionDep
) -> RuleOut:
    rule = await pricing_app.create_rule(
        session, corporation_id=user.corporation_id, **payload.model_dump()
    )
    return RuleOut(**rule.model_dump())


@router.patch("/rules/{public_id}", response_model=RuleOut)
async def update_rule(
    public_id: str,
    payload: RuleUpdateRequest,
    user: ManagerUser,
    session: SessionDep,
) -> RuleOut:
    rule = await pricing_app.update_rule(
        session,
        corporation_id=user.corporation_id,
        public_id=public_id,
        fields=payload.model_dump(exclude_unset=True),
    )
    return RuleOut(**rule.model_dump())


@router.delete("/rules/{public_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    public_id: str, user: ManagerUser, session: SessionDep
) -> None:
    await pricing_app.delete_rule(
        session, corporation_id=user.corporation_id, public_id=public_id
    )
