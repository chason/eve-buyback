from typing import Annotated

from fastapi import APIRouter, Query

from app.application import reference as reference_app
from app.interface.deps import SessionDep
from app.interface.security import RequireIdentity
from app.schemas.sde import MarketGroupOut, StationSearchResult, TypeSearchResult

router = APIRouter(tags=["reference"])


@router.get("/types/search", response_model=list[TypeSearchResult])
async def search_types(
    identity: RequireIdentity,
    session: SessionDep,
    q: Annotated[str, Query(min_length=2)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[TypeSearchResult]:
    records = await reference_app.search_types(session, query=q, limit=limit)
    return [TypeSearchResult(**r.model_dump()) for r in records]


@router.get("/stations/search", response_model=list[StationSearchResult])
async def search_stations(
    identity: RequireIdentity,
    session: SessionDep,
    q: Annotated[str, Query(min_length=2)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[StationSearchResult]:
    """Search seeded NPC stations by system or station name, for the hub picker."""
    records = await reference_app.search_stations(session, query=q, limit=limit)
    return [StationSearchResult(**r.model_dump()) for r in records]


@router.get("/market-groups", response_model=list[MarketGroupOut])
async def list_market_groups(
    identity: RequireIdentity, session: SessionDep
) -> list[MarketGroupOut]:
    records = await reference_app.list_market_groups(session)
    return [MarketGroupOut(**r.model_dump()) for r in records]
