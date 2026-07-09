import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base
from app.data.models.enums import check_enum
from app.domain.entitlements import EntitlementSource, Feature


class Entitlement(Base):
    """A corp's access to a paid feature (ADR-0042). One row per (corp, feature);
    granting/extending updates the row in place. `expires_at` NULL = perpetual grant
    (the active predicate lives in `domain/entitlements.py`). `source` records how the
    current grant came to be — `payment` (ISK reconciliation) or `admin` (an app admin's
    manual grant, ADR-0041); `granted_by_character_id` is an EVE-id audit field, set for
    admin grants and NULL for payment ones."""

    __tablename__ = "entitlements"
    __table_args__ = (
        UniqueConstraint("corporation_id", "feature", name="uq_entitlement_corp_feature"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), index=True
    )
    feature: Mapped[Feature] = mapped_column(check_enum(Feature, name="feature"))
    source: Mapped[EntitlementSource] = mapped_column(
        check_enum(EntitlementSource, name="entitlement_source")
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # BigInteger: EVE character ids can exceed 2^31 (same rationale as the roster cache).
    granted_by_character_id: Mapped[int | None] = mapped_column(
        BigInteger, default=None
    )
