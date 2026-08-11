import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base
from app.data.models.enums import check_enum
from app.domain.moves import MoveSuggestionStatus


class MoveSuggestion(Base):
    """One "looks like a move" pairing (ADR-0049, #200): the same type short at
    one configured hangar and over at another within the same sync. A decoration
    on the reconciliation artifacts it references — the shortfall flag event and
    the deemed-cost excess lot are booked regardless (the suggest-only
    invariant); this row only remembers the pattern so a human can act on it
    later. SET NULL survives the excess lot; this slice only writes `pending`."""

    __tablename__ = "move_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), index=True
    )
    type_id: Mapped[int]
    origin_location_id: Mapped[str] = mapped_column(String)
    destination_location_id: Mapped[str] = mapped_column(String)
    # The paired overlap — min(shortfall, excess); residuals keep the defaults.
    qty: Mapped[int] = mapped_column(BigInteger)
    shortfall_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reconciliation_events.id", ondelete="CASCADE")
    )
    excess_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lots.id", ondelete="SET NULL")
    )
    status: Mapped[MoveSuggestionStatus] = mapped_column(
        check_enum(MoveSuggestionStatus, name="move_suggestion_status"),
        default="pending",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
