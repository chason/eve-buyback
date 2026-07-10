from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class InstanceSetting(Base):
    """A key/value instance-level setting an app admin edits at runtime (ADR-0042) —
    operations knobs that shouldn't need a redeploy (first: the accounting-access
    price). Values are strings; the owning use case parses/validates. Config defaults
    from the environment apply when no row exists."""

    __tablename__ = "instance_settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]
