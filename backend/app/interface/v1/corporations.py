from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.application import corporations as corp_app
from app.interface.deps import SessionDep
from app.interface.security import RequireCeoOrDirector, RequireIdentity, RequireUser
from app.plugins.esi import EsiClient, get_esi_client
from app.schemas.corporation import CorporationOut, ManagerCreateRequest, ManagerOut

router = APIRouter(prefix="/corporations", tags=["corporations"])

EsiDep = Annotated[EsiClient, Depends(get_esi_client)]


@router.post("", response_model=CorporationOut, status_code=status.HTTP_201_CREATED)
async def register_corporation(
    user: RequireUser, session: SessionDep, esi: EsiDep
) -> CorporationOut:
    corp = await corp_app.register_corporation(session, esi, user)
    return CorporationOut(**corp.model_dump())


@router.get("/me", response_model=CorporationOut)
async def get_my_corporation(
    identity: RequireIdentity, session: SessionDep
) -> CorporationOut:
    corp = await corp_app.get_registered_corporation(session, identity.corporation_id)
    return CorporationOut(**corp.model_dump())


@router.get("/me/managers", response_model=list[ManagerOut])
async def list_managers(user: RequireCeoOrDirector, session: SessionDep) -> list[ManagerOut]:
    records = await corp_app.list_managers(session, user.corporation_id)
    return [ManagerOut(**r.model_dump()) for r in records]


@router.post(
    "/me/managers", response_model=ManagerOut, status_code=status.HTTP_201_CREATED
)
async def add_manager(
    payload: ManagerCreateRequest, user: RequireCeoOrDirector, session: SessionDep, esi: EsiDep
) -> ManagerOut:
    record = await corp_app.add_manager(
        session,
        esi,
        corporation_id=user.corporation_id,
        actor_character_id=user.character_id,
        target_character_id=payload.character_id,
    )
    return ManagerOut(**record.model_dump())


@router.delete("/me/managers/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_manager(
    character_id: int, user: RequireCeoOrDirector, session: SessionDep
) -> None:
    await corp_app.remove_manager(
        session, corporation_id=user.corporation_id, character_id=character_id
    )
