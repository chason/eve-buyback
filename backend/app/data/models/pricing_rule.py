from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base
from app.data.models.enums import check_enum
from app.domain.pricing import Basis, TargetKind


class PricingRule(Base):
    """A pricing override for a market group or a single type (ADR-0007). At most
    one rule per (corp, target_kind, target_id). `basis` is nullable — null inherits
    the corp config's default basis."""

    __tablename__ = "pricing_rules"
    __table_args__ = (
        UniqueConstraint(
            "corporation_id", "target_kind", "target_id", name="uq_pricing_rule_target"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    corporation_id: Mapped[int] = mapped_column(
        ForeignKey("corporations.corporation_id", ondelete="CASCADE")
    )
    target_kind: Mapped[TargetKind] = mapped_column(
        check_enum(TargetKind, name="target_kind")
    )
    target_id: Mapped[int]
    basis: Mapped[Basis | None] = mapped_column(
        check_enum(Basis, name="basis"), nullable=True
    )
    percentage: Mapped[Decimal] = mapped_column(Numeric)
    enabled: Mapped[bool] = mapped_column(default=True)
