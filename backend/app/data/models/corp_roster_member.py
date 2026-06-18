import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class CorpRosterMember(Base):
    """A character in a corporation's roster as of the last manager-roster sync
    (ADR-0036): a cached ESI snapshot the manager-designation picker searches, replaced
    wholesale on each sync. `corporation_id` is the corp UUID (ADR-0025);
    `character_eve_id` is the EVE id — these members may never have logged into the app,
    so there is no `characters` row to reference. It is a `BigInteger` because the roster
    ingests every corp member from ESI, including high (>2^31) character ids."""

    __tablename__ = "corp_roster_members"
    __table_args__ = (
        UniqueConstraint(
            "corporation_id", "character_eve_id", name="uq_roster_corp_char"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), index=True
    )
    character_eve_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str]
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
