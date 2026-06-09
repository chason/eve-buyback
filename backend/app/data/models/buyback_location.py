import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base
from app.data.models.enums import check_enum
from app.domain.locations import LocationKind


class BuybackLocation(Base):
    """An accepted buyback **drop-off** location for a corp (ADR-0030) — where members
    deliver bought-back items. Independent of the pricing hub. Either an NPC station
    (from the SDE) or a player structure; the EVE id is a string so it can hold a 64-bit
    structure id (ADR-0029). The display name (and system, for stations) is cached at
    add time so the list renders without a join/ESI hop. UUID PK (ADR-0025); one entry
    per `(corp, location)`.
    """

    __tablename__ = "buyback_locations"
    __table_args__ = (UniqueConstraint("corporation_id", "location_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE")
    )
    kind: Mapped[LocationKind] = mapped_column(
        check_enum(LocationKind, name="location_kind")
    )
    location_id: Mapped[str] = mapped_column(String)
    name: Mapped[str]
    system_name: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
