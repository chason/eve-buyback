"""Accepted buyback drop-off location endpoints (ADR-0030). Members read the list
(they pick one when appraising); only a Buyback Manager / CEO may change it."""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.application import locations as locations_app
from app.application.auth import AuthenticatedUser
from app.interface.deps import SessionDep
from app.interface.security import RequireUser, require_role
from app.schemas.locations import LocationCreateRequest, LocationOut

router = APIRouter(prefix="/corporations/me/locations", tags=["locations"])

ManagerUser = Annotated[AuthenticatedUser, Depends(require_role("manager"))]


@router.get("", response_model=list[LocationOut])
async def list_locations(
    user: RequireUser, session: SessionDep
) -> list[LocationOut]:
    records = await locations_app.list_locations(session, user.corporation_id)
    return [LocationOut.model_validate(r) for r in records]


@router.post("", response_model=LocationOut, status_code=status.HTTP_201_CREATED)
async def add_location(
    payload: LocationCreateRequest,
    user: ManagerUser,
    session: SessionDep,
) -> LocationOut:
    record = await locations_app.add_location(
        session,
        user.corporation_id,
        kind=payload.kind,
        location_id=payload.location_id,
        name=payload.name,
    )
    return LocationOut.model_validate(record)


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_location(
    location_id: str, user: ManagerUser, session: SessionDep
) -> None:
    await locations_app.remove_location(
        session, user.corporation_id, location_id=location_id
    )
