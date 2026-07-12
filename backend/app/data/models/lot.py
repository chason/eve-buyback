import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base
from app.data.models.enums import check_enum
from app.domain.lots import LotSource


class Lot(Base):
    """One acquisition of one item type at one cost basis (ADR-0043) — the central
    entity of the accounting ledger. Consumed FIFO as items sell; everything else
    (sales, transformations, future order/contract/shipment allocations) references
    lots. State is DERIVED from allocations, never stored as a column.

    Landed unit cost = `unit_purchase_cost + unit_hauling_cost`, floored to
    `written_down_to` — computed by `domain/lots.landed_unit_cost`, never stored.
    Hauling is a SELLING cost in this app (members haul in, ADR-0030), so inbound
    lots normally carry `unit_hauling_cost = 0`.

    `cost_is_estimated` marks deemed-cost lots (opening balances, hangar-reconciled
    off-app stock); it propagates through FIFO into realized profit so measured and
    estimated results never silently blend. `source_lot_id` is the ADR-0047
    provenance link for transformation children (reprocessed materials)."""

    __tablename__ = "lots"
    __table_args__ = (
        # FIFO reads are per corp + type (+ location); this is the hot path.
        Index("ix_lots_corp_type", "corporation_id", "item_type_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), index=True
    )
    item_type_id: Mapped[int]
    # BigInteger: EVE quantities exceed 2^31 (same rationale as appraisal lines).
    qty_original: Mapped[int] = mapped_column(BigInteger)
    qty_remaining: Mapped[int] = mapped_column(BigInteger)
    unit_purchase_cost: Mapped[Decimal] = mapped_column(Numeric)
    unit_hauling_cost: Mapped[Decimal] = mapped_column(Numeric, default=Decimal(0))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[LotSource] = mapped_column(check_enum(LotSource, name="lot_source"))
    # The completed appraisal this lot was born from (source='buyback'). SET NULL:
    # the lot's cost facts are copied in and survive the appraisal.
    appraisal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("appraisals.id", ondelete="SET NULL")
    )
    # Transformation provenance (ADR-0047): the lot this one was reprocessed from.
    source_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lots.id", ondelete="SET NULL")
    )
    cost_is_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    # EVE station or structure id as a string (ADR-0029 convention); NULL = unknown.
    location_id: Mapped[str | None] = mapped_column(String)
    # Per-unit NRV a write-down was taken to (ADR-0043); NULL = never written down.
    # Only ever set downward; never reversed up.
    written_down_to: Mapped[Decimal | None] = mapped_column(Numeric)
    notes: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
