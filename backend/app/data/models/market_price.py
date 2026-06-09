from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class MarketPrice(Base):
    """A cached Fuzzwork aggregate for one item at one market hub (ADR-0006).

    Keyed by `(hub_id, type_id)`. We store the full set of buy/sell aggregate
    fields Fuzzwork returns so the per-corp `aggregate_field` choice (M5) can pick
    any of them without a re-fetch. ISK and volume values are `Numeric`/`Decimal`
    (exact arbitrary precision on Postgres; ADR-0020) — no float drift in money
    math. `fetched_at` drives the TTL; the application layer sets it explicitly so
    freshness math is exact.
    """

    __tablename__ = "market_prices"

    # hub_id is an EVE location id stored as a string — a station id or a 64-bit
    # player structure id — so it's free of int32/JS-number range concerns (ADR-0029).
    hub_id: Mapped[str] = mapped_column(String, primary_key=True)
    type_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)

    buy_weighted_average: Mapped[Decimal] = mapped_column(Numeric)
    buy_max: Mapped[Decimal] = mapped_column(Numeric)
    buy_min: Mapped[Decimal] = mapped_column(Numeric)
    buy_median: Mapped[Decimal] = mapped_column(Numeric)
    buy_percentile: Mapped[Decimal] = mapped_column(Numeric)
    buy_volume: Mapped[Decimal] = mapped_column(Numeric)
    buy_order_count: Mapped[int]

    sell_weighted_average: Mapped[Decimal] = mapped_column(Numeric)
    sell_max: Mapped[Decimal] = mapped_column(Numeric)
    sell_min: Mapped[Decimal] = mapped_column(Numeric)
    sell_median: Mapped[Decimal] = mapped_column(Numeric)
    sell_percentile: Mapped[Decimal] = mapped_column(Numeric)
    sell_volume: Mapped[Decimal] = mapped_column(Numeric)
    sell_order_count: Mapped[int]

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
