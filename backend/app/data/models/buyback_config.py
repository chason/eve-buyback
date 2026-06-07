from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base
from app.data.models.enums import check_enum
from app.domain.pricing import AggregateField, Basis


class BuybackConfig(Base):
    """Per-corp pricing defaults — the "global" rule (ADR-0007). One row per
    registered corporation, created at registration. Closed-set columns are
    CHECK-constrained from the domain Literals (ADR-0021); money is Numeric (ADR-0020).
    """

    __tablename__ = "buyback_configs"

    corporation_id: Mapped[int] = mapped_column(
        ForeignKey("corporations.corporation_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    market_hub_id: Mapped[int]
    default_basis: Mapped[Basis] = mapped_column(check_enum(Basis, name="basis"))
    default_percentage: Mapped[Decimal] = mapped_column(Numeric)
    aggregate_field: Mapped[AggregateField] = mapped_column(
        check_enum(AggregateField, name="aggregate_field")
    )
