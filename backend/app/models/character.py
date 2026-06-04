from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Character(Base):
    """An EVE character we've seen log in (referenceable by manager grants)."""

    __tablename__ = "characters"

    character_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
