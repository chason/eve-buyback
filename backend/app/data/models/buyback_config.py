import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base
from app.data.models.enums import check_enum
from app.domain.market import HubKind
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
    # Hub kind + ESI region (ADR-0028). The 5 Fuzzwork hubs are npc_station with no
    # stored region (Fuzzwork is keyed by station). A non-Fuzzwork NPC station caches
    # its region_id (for ESI region-orders) and a display name, both resolved at save.
    market_hub_kind: Mapped[HubKind] = mapped_column(
        check_enum(HubKind, name="market_hub_kind"),
        default="npc_station",
        server_default=text("'npc_station'"),
    )
    market_region_id: Mapped[int | None] = mapped_column(default=None)
    market_hub_name: Mapped[str | None] = mapped_column(default=None)
    default_basis: Mapped[Basis] = mapped_column(check_enum(Basis, name="basis"))
    default_percentage: Mapped[Decimal] = mapped_column(Numeric)
    aggregate_field: Mapped[AggregateField] = mapped_column(
        check_enum(AggregateField, name="aggregate_field")
    )
    # The global default's accept flag: False → buy nothing unless a rule accepts it.
    default_accepted: Mapped[bool] = mapped_column(
        default=True, server_default=text("true")
    )
