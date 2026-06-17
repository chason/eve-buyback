import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class StructureMarketToken(Base):
    """A persisted, Fernet-encrypted EVE refresh token authorizing structure-market
    reads for a corp's structure hub (ADR-0029). One per corporation. Only the
    ciphertext is stored; access tokens are never persisted. The authorizing
    character is recorded (UUID FK + denormalized eve id/name) for display + audit.
    """

    __tablename__ = "structure_market_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), unique=True
    )
    character_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("characters.id"))
    character_eve_id: Mapped[int]
    character_name: Mapped[str]
    encrypted_refresh_token: Mapped[bytes] = mapped_column(LargeBinary)
    scopes: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Set when a refresh fails (revoked grant / lost access); surfaced in the status.
    last_refresh_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # Bumped each time this token is used to fetch a structure book in the background
    # refresh (ADR-0034, #88), so the next cycle's token selection rotates to the
    # least-recently-used corp rather than always leaning on the same one.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
