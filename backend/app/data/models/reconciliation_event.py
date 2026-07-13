import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base
from app.data.models.enums import check_enum
from app.domain.reconciliation import ReconciliationKind


class ReconciliationEvent(Base):
    """One thing a hangar sync changed or flagged (ADR-0044) — the reconciliation
    log behind the "Needs a look" list. An `excess` normally carries the deemed-cost
    lot it created (`lot_id`; SET NULL survives the lot); an excess with no lot
    couldn't be priced yet and stays flagged. A `shortfall` never creates anything —
    it's always a flag for a human. Append-only."""

    __tablename__ = "reconciliation_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), index=True
    )
    location_id: Mapped[str] = mapped_column(String)
    type_id: Mapped[int]
    kind: Mapped[ReconciliationKind] = mapped_column(
        check_enum(ReconciliationKind, name="reconciliation_kind")
    )
    # Magnitude of the difference (always positive; `kind` is the direction).
    qty: Mapped[int] = mapped_column(BigInteger)
    # The deemed unit cost an excess was booked at; NULL for shortfalls and for
    # excess that couldn't be priced.
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric)
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lots.id", ondelete="SET NULL")
    )
    # Needs a human look: every shortfall, unpriceable excess, and excess whose
    # deemed value crosses the anomaly threshold (ADR-0044).
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None]
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
