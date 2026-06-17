"""Read/write the per-hub background-refresh marker (ADR-0034, #70).

Records when a hub's full order book was last fetched, so an illiquid structure whose
book comes back empty (nothing written to `market_prices`) isn't re-fetched every
cycle. The application layer owns the clock and the `commit()`.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import MarketHubRefresh


async def get_refreshed_at(
    session: AsyncSession, *, hub_id: str
) -> datetime | None:
    """When this hub's book was last refreshed, or None if it never has been."""
    stmt = select(MarketHubRefresh.refreshed_at).where(
        MarketHubRefresh.hub_id == hub_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def mark_refreshed(
    session: AsyncSession, *, hub_id: str, at: datetime
) -> None:
    """Stamp the hub's last-refreshed time (insert-or-update)."""
    stmt = pg_insert(MarketHubRefresh).values(hub_id=hub_id, refreshed_at=at)
    stmt = stmt.on_conflict_do_update(
        index_elements=[MarketHubRefresh.hub_id],
        set_={"refreshed_at": stmt.excluded.refreshed_at},
    )
    await session.execute(stmt)
