from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class MarketHubRefresh(Base):
    """When the background job last fetched a hub's full order book (ADR-0034, #70).

    The structure refresh decides "is this hub due?" from its freshest cached price
    (`market_prices.fetched_at`). But a successful fetch of an *empty* book writes no
    price rows, so that signal would stay null and the (costliest) whole-book fetch
    would repeat every cycle forever. This marker is stamped on every successful
    structure fetch — empty or not — so an illiquid structure settles after one fetch.
    Keyed by the EVE location id string, like `market_prices.hub_id`.
    """

    __tablename__ = "market_hub_refreshes"

    hub_id: Mapped[str] = mapped_column(String, primary_key=True)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
