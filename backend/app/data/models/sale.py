import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Numeric,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base
from app.data.models.enums import check_enum
from app.domain.lots import EntrySource, SaleChannel


class Sale(Base):
    """One realized sale event against ONE lot (ADR-0043/0045) — the only place
    revenue is ever recorded. A market fill or contract that draws from several lots
    writes one row per lot touched, so COGS is exact per lot. Realized profit is
    derived (`domain/lots.realized_profit`), never stored.

    `external_ref` is the EVE-side idempotency key — the wallet `transaction_id` for
    market fills, the `contract_id` for contract sales — unique per channel so
    re-polling never double-records; NULL for manual (off-game) sales. `source` is
    provenance (esi | manual, ADR-0045) — orthogonal to the lot's
    `cost_is_estimated`, which rides in via the lot."""

    __tablename__ = "sales"
    __table_args__ = (
        UniqueConstraint(
            "channel", "external_ref", "lot_id", name="uq_sale_channel_ref_lot"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), index=True
    )
    lot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lots.id", ondelete="CASCADE"))
    qty: Mapped[int] = mapped_column(BigInteger)
    unit_proceeds: Mapped[Decimal] = mapped_column(Numeric)
    # Tax attributed to THIS row's share of the fill (a multi-lot fill splits it).
    sales_tax: Mapped[Decimal] = mapped_column(Numeric, default=Decimal(0))
    channel: Mapped[SaleChannel] = mapped_column(
        check_enum(SaleChannel, name="sale_channel")
    )
    source: Mapped[EntrySource] = mapped_column(
        check_enum(EntrySource, name="entry_source")
    )
    external_ref: Mapped[int | None] = mapped_column(BigInteger)
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # EVE character id of the manager for manual entries; NULL for ESI-detected rows.
    recorded_by_character_id: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
