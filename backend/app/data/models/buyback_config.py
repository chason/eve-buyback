import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base
from app.data.models.enums import check_enum
from app.domain.pricing import AggregateField, Basis


class BuybackConfig(Base):
    """Per-corp pricing defaults — the "global" rule (ADR-0007). One row per
    registered corporation (FK = corporation UUID, ADR-0025), created at registration.
    Closed-set columns are CHECK-constrained (ADR-0021); money is Numeric (ADR-0020).
    """

    __tablename__ = "buyback_configs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), unique=True
    )
    market_hub_id: Mapped[int]
    default_basis: Mapped[Basis] = mapped_column(check_enum(Basis, name="basis"))
    default_percentage: Mapped[Decimal] = mapped_column(Numeric)
    aggregate_field: Mapped[AggregateField] = mapped_column(
        check_enum(AggregateField, name="aggregate_field")
    )
    # The global default's accept flag: False → buy nothing unless a rule accepts it.
    default_accepted: Mapped[bool] = mapped_column(
        default=True, server_default=text("true")
    )
