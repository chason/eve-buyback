import uuid
from datetime import datetime

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class Corporation(Base):
    """A registered corporation (the tenant). UUID identity; the EVE corporation id is
    a unique attribute (ADR-0025). `ceo_character_id`/`registered_by_character_id` are
    EVE-id audit fields, not FKs."""

    __tablename__ = "corporations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    eve_id: Mapped[int] = mapped_column(unique=True)
    name: Mapped[str]
    ceo_character_id: Mapped[int]
    registered_by_character_id: Mapped[int]
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
