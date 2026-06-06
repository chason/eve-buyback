from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class Corporation(Base):
    """A registered corporation (the tenant). Keyed by the EVE corporation id."""

    __tablename__ = "corporations"

    corporation_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    ceo_character_id: Mapped[int]
    registered_by_character_id: Mapped[int]
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
