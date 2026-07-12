import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class LotTransformation(Base):
    """One reprocess event (ADR-0047): `qty_consumed` units left `source_lot_id` and
    became child lots (which point back via `lots.source_lot_id`). The children's
    combined cost basis equals exactly the source cost consumed — allocation happens
    in the use case; this row is the audit record that the event occurred.

    Schema hook shipped with the base ledger (#150); the recording behavior (manual
    action + hangar-reconciliation suggestion) lands with #177."""

    __tablename__ = "lot_transformations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), index=True
    )
    source_lot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lots.id", ondelete="CASCADE")
    )
    qty_consumed: Mapped[int] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # EVE character id of the manager who recorded it (manual entries); NULL when a
    # future automated path records one.
    recorded_by_character_id: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
