import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class AppraisalContract(Base):
    """The current best EVE contract matched to an appraisal plus its derived status
    (ADR-0037 contract watcher). A **mutable** side-table — the appraisal itself is
    write-once (ADR-0014) — refreshed by the background contract watcher, so contract
    state never touches the immutable appraisal row.

    One row per appraisal (`appraisal_id` unique). `corporation_id` is the corp UUID
    (ADR-0025); `contract_id` is the EVE contract id (`BigInteger`). `status` is a plain
    string — `in_progress | completed | rejected | cancelled | expired | failed |
    mismatch` (the codebase stores statuses as strings; the Literal lives in
    `domain/contracts.py`)."""

    __tablename__ = "appraisal_contracts"
    __table_args__ = (
        UniqueConstraint(
            "corporation_id", "contract_id", name="uq_appraisal_contract_corp_contract"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    appraisal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("appraisals.id", ondelete="CASCADE"), unique=True
    )
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), index=True
    )
    contract_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str]
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
