import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class Receivable(Base):
    """ISK the buyback is owed (ADR-0045, #158): a sale paid into the wrong corp
    wallet or a personal wallet is a CASH-LOCATION problem, not missing revenue —
    the sale event already recognized the revenue, and this row keeps the amount an
    honest asset until the ISK actually moves. Clearing it (when the transfer
    happens) closes the loop without double-counting; a receivable is never revenue
    itself. Append-only + a clear timestamp — no edits, no deletes."""

    __tablename__ = "receivables"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric)
    # Who owes it / where the ISK sits — the manager's words; required.
    note: Mapped[str]
    incurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entered_by_character_id: Mapped[int] = mapped_column(BigInteger)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleared_by_character_id: Mapped[int | None] = mapped_column(BigInteger)
    cleared_note: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
