from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base
from app.data.models.enums import check_enum
from app.domain.pricing import Basis, LineStatus


class AppraisalLine(Base):
    """A per-line snapshot within an appraisal (ADR-0014). Write-once; stores the
    resolved basis/percentage and market unit value so the line is self-explanatory
    without re-reading rules or prices. Rejected lines have line_total 0, a reason,
    and null pricing fields."""

    __tablename__ = "appraisal_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    appraisal_id: Mapped[int] = mapped_column(
        ForeignKey("appraisals.id", ondelete="CASCADE")
    )
    # Nullable: a pasted item name we couldn't resolve has no type_id — it's stored
    # as a rejected line with just its `type_name` (ADR-0021).
    type_id: Mapped[int | None]
    type_name: Mapped[str]
    quantity: Mapped[int]
    status: Mapped[LineStatus] = mapped_column(
        check_enum(LineStatus, name="line_status")
    )
    basis: Mapped[Basis | None] = mapped_column(
        check_enum(Basis, name="basis"), nullable=True
    )
    percentage: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    unit_value: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    line_total: Mapped[Decimal] = mapped_column(Numeric)
    reason: Mapped[str | None] = mapped_column(nullable=True)
