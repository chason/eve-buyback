import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base
from app.data.models.enums import check_enum
from app.domain.lots import EntrySource, ExpenseKind


class LotExpense(Base):
    """A cost not embedded in a lot's basis (ADR-0043/0045): broker/relist fees,
    outbound hauling (a SELLING cost in this app — members haul in, ADR-0030),
    write-down losses, and anything a manager books by hand. Attributed to a lot when
    one is identifiable so per-item margins stay honest; corp-level otherwise.

    `external_ref` is the EVE journal id for ESI-detected fees (idempotency); NULL
    for manual entries. Corrections are REVERSING entries (a negative amount with a
    note), never edits — the audit trail is the trust (ADR-0045)."""

    __tablename__ = "expenses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[ExpenseKind] = mapped_column(check_enum(ExpenseKind, name="expense_kind"))
    amount: Mapped[Decimal] = mapped_column(Numeric)
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lots.id", ondelete="SET NULL")
    )
    source: Mapped[EntrySource] = mapped_column(
        check_enum(EntrySource, name="entry_source")
    )
    external_ref: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    incurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_by_character_id: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
