import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base
from app.data.models.enums import check_enum
from app.domain.pricing import Basis, TargetKind


class PricingRule(Base):
    """A pricing override for a market group or a single type (ADR-0007). At most
    one rule per (corp, target_kind, target_id) — the rule is addressed externally by
    that natural key, never by the UUID PK (ADR-0022/0025). `basis` null inherits the
    corp config's default basis. `reprocess` prices a matched **ore** by its refined
    mineral value rather than its own market price (ADR-0026); ignored for non-ores."""

    __tablename__ = "pricing_rules"
    __table_args__ = (
        UniqueConstraint(
            "corporation_id", "target_kind", "target_id", name="uq_pricing_rule_target"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE")
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
    reprocess: Mapped[bool] = mapped_column(
        default=False, server_default=text("false")
    )
    # Accept only the compressed variants of matched ores (ADR-0026); ore-only.
    compressed_only: Mapped[bool] = mapped_column(
        default=False, server_default=text("false")
    )
    # False → the buyback rejects matching items (a blacklist rule).
    accepted: Mapped[bool] = mapped_column(
        default=True, server_default=text("true")
    )
