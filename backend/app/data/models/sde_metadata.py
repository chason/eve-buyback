from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class SdeMetadata(Base):
    """A single-row stamp recording the last SDE seed (ADR-0009: version-stamp
    each import). Always row id 1, overwritten on each successful seed."""

    __tablename__ = "sde_metadata"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    source: Mapped[str]
    type_count: Mapped[int]
    market_group_count: Mapped[int]
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
