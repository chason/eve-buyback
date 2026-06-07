from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class SdeMarketGroup(Base):
    """A node in the EVE market-group hierarchy, seeded from the SDE (ADR-0009).

    Reference data — shared across all corps, no `corp_id`. `parent_id` is the
    parent market group (None at the roots); there is deliberately no FK, since a
    parent may be inserted after its children during a bulk seed.
    """

    __tablename__ = "sde_market_groups"

    market_group_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    parent_id: Mapped[int | None]
    name: Mapped[str]
