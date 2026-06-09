from sqlalchemy.orm import Mapped, mapped_column

from app.data.db import Base


class SdeStation(Base):
    """An NPC station, seeded from the SDE (ADR-0009) for the hub picker (ADR-0028).

    Reference data, EVE-keyed by `station_id`. `name` and `system_name` are indexed
    for the station search; `region_id` lets the config resolve a hub's region for
    ESI pricing without an ESI round-trip. Player structures are **not** in the SDE
    and are not stored here.
    """

    __tablename__ = "sde_stations"

    station_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(index=True)
    system_name: Mapped[str] = mapped_column(index=True)
    region_id: Mapped[int]
