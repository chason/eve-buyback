# Import models so Alembic's autogenerate sees their metadata via data.db.Base.
from app.data.models.character import Character
from app.data.models.corporation import Corporation
from app.data.models.manager_assignment import ManagerAssignment
from app.data.models.sde_market_group import SdeMarketGroup
from app.data.models.sde_metadata import SdeMetadata
from app.data.models.sde_type import SdeType

__all__ = [
    "Character",
    "Corporation",
    "ManagerAssignment",
    "SdeMarketGroup",
    "SdeMetadata",
    "SdeType",
]
