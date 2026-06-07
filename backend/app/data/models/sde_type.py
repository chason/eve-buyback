from decimal import Decimal

from sqlalchemy import Numeric, text
from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class SdeType(Base):
    """A market-tradeable EVE item type, seeded from the SDE (ADR-0009).

    Reference data — shared across all corps. The seed keeps only published types
    that have a `market_group_id` (the things a buyback can quote), so this table
    stays small. `name` is indexed for the type-search picker. `volume` (m³) is
    `Numeric`/`Decimal` for exact precision (ADR-0020).

    `category_id` (the type's group's category) flags ores — category 25 (Asteroid)
    — for reprocess pricing (ADR-0026); `portion_size` is the refine batch size.
    """

    __tablename__ = "sde_types"

    type_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(index=True)
    group_id: Mapped[int]
    category_id: Mapped[int | None]
    market_group_id: Mapped[int | None]
    volume: Mapped[Decimal] = mapped_column(Numeric)
    portion_size: Mapped[int] = mapped_column(server_default=text("1"))
    published: Mapped[bool]
