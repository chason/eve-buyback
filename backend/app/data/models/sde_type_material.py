from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class SdeTypeMaterial(Base):
    """Perfect-refine (100% base) reprocessing yield for a type, seeded from the
    SDE's `invTypeMaterials` (ADR-0026). Reference data, EVE-keyed like the other
    SDE tables. The seed stores materials for **every seeded type** — reprocess
    recording is source-agnostic (ADR-0047), and a type with no rows here cannot
    be reprocessed at all. `quantity` is the base material count per refine batch
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
