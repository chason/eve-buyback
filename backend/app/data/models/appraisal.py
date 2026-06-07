from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class Appraisal(Base):
    """A persisted, immutable appraisal snapshot (ADR-0014). Write-once: never
    edited or recomputed. `public_id` is a random, non-sequential share handle."""

    __tablename__ = "appraisals"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(unique=True, index=True)
    corporation_id: Mapped[int] = mapped_column(
        ForeignKey("corporations.corporation_id", ondelete="CASCADE")
    )
    created_by_character_id: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    market_hub_id: Mapped[int]
    accepted_total: Mapped[Decimal] = mapped_column(Numeric)
    rejected_count: Mapped[int]

    # Log-only audit copy of the original request payload (ADR-0021). This field is
    # for documentation/debugging and MUST NOT be queried under normal operation —
    # the authoritative line data lives in `appraisal_lines`.
    request_json: Mapped[dict] = mapped_column(JSON)
