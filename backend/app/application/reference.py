"""SDE reference read use cases (the type-search and market-group pickers). Thin
pass-throughs that keep the interface→application→data boundary honest; read-only,
no unit of work."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.records import SdeMarketGroupRecord, SdeTypeRecord
from app.data.repositories import sde as sde_repo


async def search_types(
    session: AsyncSession, *, query: str, limit: int
) -> list[SdeTypeRecord]:
    return await sde_repo.search_types(session, query, limit)


async def list_market_groups(session: AsyncSession) -> list[SdeMarketGroupRecord]:
    return await sde_repo.list_market_groups(session)
