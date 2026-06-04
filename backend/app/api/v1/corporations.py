from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.auth.session import RequireUser, require_role, set_session_user
from app.deps import SessionDep
from app.eve.esi import EsiClient, get_esi_client
from app.models import Character, Corporation, ManagerAssignment
from app.schemas.auth import Role, SessionUser
from app.schemas.corporation import CorporationOut, ManagerCreateRequest, ManagerOut

router = APIRouter(prefix="/corporations", tags=["corporations"])

EsiDep = Annotated[EsiClient, Depends(get_esi_client)]
CeoUser = Annotated[SessionUser, Depends(require_role("ceo"))]


async def get_current_corporation(
    user: RequireUser, session: SessionDep
) -> Corporation:
    corp = await session.get(Corporation, user.corporation_id)
    if corp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Corporation is not registered",
        )
    return corp


CurrentCorporation = Annotated[Corporation, Depends(get_current_corporation)]


@router.post("", response_model=CorporationOut, status_code=status.HTTP_201_CREATED)
async def register_corporation(
    request: Request, user: RequireUser, session: SessionDep, esi: EsiDep
) -> Corporation:
    """Register the caller's corporation. Allowed for the CEO or a Director (ADR-0015)."""
    if not (user.role == "ceo" or user.is_director):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the CEO or a Director can register the corporation",
        )
    if await session.get(Corporation, user.corporation_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Corporation is already registered",
        )

    info = await esi.get_corporation(user.corporation_id)
    corp = Corporation(
        corporation_id=user.corporation_id,
        name=info.name,
        ceo_character_id=info.ceo_id,
        registered_by_character_id=user.character_id,
    )
    session.add(corp)

    # A non-CEO Director who registers is auto-granted Buyback Manager (ADR-0015).
    new_role: Role = user.role
    if user.role != "ceo":
        session.add(
            ManagerAssignment(
                corporation_id=user.corporation_id,
                character_id=user.character_id,
                granted_by_character_id=user.character_id,
            )
        )
        new_role = "manager"

    await session.commit()
    await session.refresh(corp)

    set_session_user(
        request,
        user.model_copy(update={"corporation_registered": True, "role": new_role}),
    )
    return corp


@router.get("/me", response_model=CorporationOut)
async def get_my_corporation(corp: CurrentCorporation) -> Corporation:
    return corp


@router.get("/me/managers", response_model=list[ManagerOut])
async def list_managers(
    _user: CeoUser, corp: CurrentCorporation, session: SessionDep
) -> list[ManagerOut]:
    stmt = (
        select(ManagerAssignment, Character.name)
        .join(Character, Character.character_id == ManagerAssignment.character_id)
        .where(ManagerAssignment.corporation_id == corp.corporation_id)
    )
    rows = (await session.execute(stmt)).all()
    return [
        ManagerOut(
            character_id=assignment.character_id,
            character_name=name,
            granted_by_character_id=assignment.granted_by_character_id,
            granted_at=assignment.granted_at,
        )
        for assignment, name in rows
    ]


@router.post(
    "/me/managers", response_model=ManagerOut, status_code=status.HTTP_201_CREATED
)
async def add_manager(
    payload: ManagerCreateRequest,
    user: CeoUser,
    corp: CurrentCorporation,
    session: SessionDep,
    esi: EsiDep,
) -> ManagerOut:
    target = await esi.get_character(payload.character_id)
    if target.corporation_id != corp.corporation_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Character is not a member of your corporation",
        )

    char = await session.get(Character, payload.character_id)
    if char is None:
        session.add(Character(character_id=payload.character_id, name=target.name))
    else:
        char.name = target.name

    existing = await session.execute(
        select(ManagerAssignment).where(
            ManagerAssignment.corporation_id == corp.corporation_id,
            ManagerAssignment.character_id == payload.character_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Character is already a manager",
        )

    assignment = ManagerAssignment(
        corporation_id=corp.corporation_id,
        character_id=payload.character_id,
        granted_by_character_id=user.character_id,
    )
    session.add(assignment)
    await session.commit()
    await session.refresh(assignment)
    return ManagerOut(
        character_id=payload.character_id,
        character_name=target.name,
        granted_by_character_id=user.character_id,
        granted_at=assignment.granted_at,
    )


@router.delete(
    "/me/managers/{character_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_manager(
    character_id: int, _user: CeoUser, corp: CurrentCorporation, session: SessionDep
) -> None:
    result = await session.execute(
        select(ManagerAssignment).where(
            ManagerAssignment.corporation_id == corp.corporation_id,
            ManagerAssignment.character_id == character_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if assignment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Manager not found"
        )
    await session.delete(assignment)
    await session.commit()
