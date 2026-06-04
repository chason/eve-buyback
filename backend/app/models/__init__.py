# Import models so Alembic's autogenerate sees their metadata via app.db.Base.
from app.models.character import Character
from app.models.corporation import Corporation
from app.models.manager_assignment import ManagerAssignment

__all__ = ["Character", "Corporation", "ManagerAssignment"]
