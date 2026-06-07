import uuid
from datetime import datetime

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class Character(Base):
    """An EVE character we've seen log in (referenceable by manager grants). The row
    identity is a UUID; the EVE character id is a unique attribute (ADR-0025)."""

    __tablename__ = "characters"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    eve_id: Mapped[int] = mapped_column(unique=True)
    name: Mapped[str]
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
