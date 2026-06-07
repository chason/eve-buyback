from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class SdeTypeMaterial(Base):
    """Perfect-refine (100% base) reprocessing yield for a type, seeded from the
    SDE's `invTypeMaterials` (ADR-0026). Reference data, EVE-keyed like the other
    SDE tables. The seed stores only **ore** types' materials (category 25) to keep
    the table small. `quantity` is the base material count per refine batch
    (`SdeType.portion_size` units); the pricing engine scales it by the ore yield.
    """

    __tablename__ = "sde_type_materials"

    type_id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=False
    )
    material_type_id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=False
    )
    quantity: Mapped[int]
