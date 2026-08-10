import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class CorpMarketOrder(Base):
    """Snapshot of one LIVE corp market order (ADR-0045, #156), refreshed by each
    sales-ingestion pass (replace-per-corp). Sell orders are the "listed" state:
    their escrowed units have physically left the hangar, so the reconciliation
    subtracts them from expected stock (`qty_idle`). `wallet_division` backs the
    unexpected-division guard. A snapshot, not history — vanished orders vanish."""

    __tablename__ = "corp_market_orders"
    __table_args__ = (UniqueConstraint("corporation_id", "order_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), index=True
    )
    order_id: Mapped[int] = mapped_column(BigInteger)
    type_id: Mapped[int]
    is_buy_order: Mapped[bool] = mapped_column(Boolean, default=False)
    price: Mapped[Decimal] = mapped_column(Numeric)
    volume_remain: Mapped[int] = mapped_column(BigInteger)
    volume_total: Mapped[int] = mapped_column(BigInteger)
    # EVE station/structure id as a string (ADR-0029 convention).
    location_id: Mapped[str] = mapped_column(String)
    wallet_division: Mapped[int]
    issued: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
