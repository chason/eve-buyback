import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class ManagerAssignment(Base):
    """Grants the Buyback Manager role to a character within a corporation. FKs are
    the corporation/character UUIDs (ADR-0025); `granted_by_character_id` is an EVE-id
    audit field."""

    __tablename__ = "manager_assignments"
    __table_args__ = (
        UniqueConstraint("corporation_id", "character_id", name="uq_manager_corp_char"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE")
    )
    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE")
    )
    granted_by_character_id: Mapped[int]
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
