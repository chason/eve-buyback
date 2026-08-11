import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class HangarSync(Base):
    """When the corp's hangar snapshot (`hangar_stock`, ADR-0050) was last taken.
    A separate one-row marker rather than a timestamp on the stock rows because an
    EMPTY hangar is a valid snapshot: zero stock rows plus a fresh `synced_at` means
    "we looked and it's empty", while no row here means "we've never looked" — the
    Stock page falls back to the ledger view in the second case only."""

    __tablename__ = "hangar_sync"

    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), primary_key=True
    )
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
