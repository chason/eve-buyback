import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class WalletPayment(Base):
    """One incoming ISK transfer seen in the operator's wallet journal (ADR-0042).
    `journal_id` (EVE's journal entry id) is unique, so re-polling never records a
    payment twice. Matched payments carry the corp whose access they extended and how
    many periods were granted; unmatched ones (no/unknown reference, or an amount below
    the price) stay for the admin to match by hand. Match state is derived from
    `matched_corporation_id` — not a stored status."""

    __tablename__ = "wallet_payments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    journal_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric)
    # The sender (journal first_party): character for player donations, corp for corp
    # wallet withdrawals. BigInteger: EVE ids exceed 2^31. Name resolved best-effort.
    sender_eve_id: Mapped[int | None] = mapped_column(BigInteger)
    sender_name: Mapped[str | None]
    reason: Mapped[str | None]
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Set when the payment extended a corp's access (auto or by admin action). SET NULL
    # keeps the payment audit row even if the corp is ever deleted.
    matched_corporation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("corporations.id", ondelete="SET NULL")
    )
    periods_granted: Mapped[int] = mapped_column(default=0)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # EVE character id of the admin for a manual match; NULL for automatic matches.
    matched_by_character_id: Mapped[int | None] = mapped_column(BigInteger)
