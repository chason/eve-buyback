import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class BuybackHangar(Base):
    """One corp hangar division the accounting treats as "the buyback hangar"
    (ADR-0044) — where the reconciliation counts physical stock. The location is one
    of the corp's drop-off locations (ADR-0030); its display name is snapshotted at
    add time (like `buyback_locations`) so the list renders without a join and
    survives the drop-off row's deletion. `division` 1..7 maps to the ESI
    `location_flag` CorpSAG1..CorpSAG7."""

    __tablename__ = "buyback_hangars"
    __table_args__ = (
        UniqueConstraint("corporation_id", "location_id", "division"),
        CheckConstraint("division BETWEEN 1 AND 7", name="hangar_division_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), index=True
    )
    location_id: Mapped[str] = mapped_column(String)
    location_name: Mapped[str]
    division: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
