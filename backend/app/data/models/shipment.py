import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base
from app.data.models.enums import check_enum
from app.domain.shipments import ShipmentStatus


class Shipment(Base):
    """One declared haul (ADR-0049, #208): a manager sent `qty` of a type from
    one marked hangar to another. While `open` it IS the in-transit allocation
    (ADR-0043): the reconciliation excludes the quantity from idle at the origin
    and treats it as spoken-for at the destination, so the haul is never flagged
    and never feeds a move pair. Aggregate by design — like the sell-order
    snapshot, no per-lot rows: lots stay put until arrival relocates them FIFO
    (the `confirm_move` mechanic), so mid-transit sales or splits can never
    strand a stale lot reference."""

    __tablename__ = "shipments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), index=True
    )
    type_id: Mapped[int]
    origin_location_id: Mapped[str] = mapped_column(String)
    destination_location_id: Mapped[str] = mapped_column(String)
    qty: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[ShipmentStatus] = mapped_column(
        check_enum(ShipmentStatus, name="shipment_status"), default="open"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
