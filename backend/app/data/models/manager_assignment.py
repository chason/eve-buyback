from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class ManagerAssignment(Base):
    """Grants the Buyback Manager role to a character within a corporation."""

    __tablename__ = "manager_assignments"
    __table_args__ = (
        UniqueConstraint("corporation_id", "character_id", name="uq_manager_corp_char"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    corporation_id: Mapped[int] = mapped_column(
        ForeignKey("corporations.corporation_id", ondelete="CASCADE")
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.character_id", ondelete="CASCADE")
    )
    granted_by_character_id: Mapped[int]
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
