import uuid

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class HangarStock(Base):
    """One counted stack in the last hangar snapshot (ADR-0050): the `(location,
    type) → quantity` a hangar sync actually saw across the corp's marked buyback
    hangars, folded over divisions and containers exactly as the reconciliation
    counts them. Replaced wholesale on every successful sync — this is a photograph
    of the hangar, not a ledger — so the Stock page can show what's physically
    there without an ESI call. Only type + quantity are kept (the privacy page's
    promise); item ids and container structure are discarded at fetch time."""

    __tablename__ = "hangar_stock"
    __table_args__ = (UniqueConstraint("corporation_id", "location_id", "type_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    corporation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corporations.id", ondelete="CASCADE"), index=True
    )
    location_id: Mapped[str] = mapped_column(String)
    type_id: Mapped[int]
    qty: Mapped[int] = mapped_column(BigInteger)
