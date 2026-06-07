import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class Appraisal(Base):
    """A persisted, immutable appraisal snapshot (ADR-0014). Write-once: never
    edited or recomputed. UUID PK (ADR-0025); `public_id` is the random, non-sequential
    share handle; `corporation_id` is the corp UUID FK; `created_by_character_id` is an
    EVE-id audit field."""

    __tablename__ = "appraisals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    public_id: Mapped[str] = mapped_column(unique=True, index=True)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE")
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
