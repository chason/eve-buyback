import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base
from app.data.models.enums import check_enum
from app.domain.moves import MoveSuggestionStatus


class MoveSuggestion(Base):
    """One "looks like a move" pairing (ADR-0049, #200/#206): the same type
    short at one configured hangar, with excess evidence at another location —
    stock counted in a hangar beyond the books, and/or the division's
    sell-order escrow there beyond what idle lots explain. A decoration on the
    reconciliation artifacts it references — the shortfall flag event and the
    deemed-cost excess lot are booked regardless (the suggest-only invariant);
    this row only remembers the pattern so a human can act on it later.
    SET NULL survives the excess lot; the status lifecycle is documented on
    `MoveSuggestionStatus` (domain/moves.py)."""

    __tablename__ = "move_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), index=True
    )
    type_id: Mapped[int]
    origin_location_id: Mapped[str] = mapped_column(String)
    destination_location_id: Mapped[str] = mapped_column(String)
    # The paired overlap — min(shortfall, total excess); residuals keep the
    # defaults. `qty_listed` of it is the sell-order-escrow portion (#206) and
    # `qty_sold` the already-sold-at-estimated-cost portion (#207); the rest is
    # hangar-counted excess backed by `excess_lot_id`.
    qty: Mapped[int] = mapped_column(BigInteger)
    qty_listed: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    qty_sold: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    # What the confirmation actually retired for the sold portion (#207) — 0
    # until confirmed, and at most `qty_sold` (the origin may hold less by
    # confirm time). Summed per (destination, type) so retired quantity drops
    # out of future pairings; sale rows are frozen and can't carry this state.
    qty_retired: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    shortfall_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reconciliation_events.id", ondelete="CASCADE")
    )
    # NULL when the pair has no counted portion — pure sell-side evidence has
    # no deemed lot to decorate (#206) — or when the lot was deleted (SET NULL).
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
