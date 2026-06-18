from datetime import datetime

from pydantic import BaseModel


class RosterStatusOut(BaseModel):
    """The corp roster's sync state, shown on the Config panel + Managers page."""

    synced: bool
    synced_at: datetime | None = None
    member_count: int = 0


class CorpMemberOut(BaseModel):
    """A corp member matched by the manager-designation search."""

    character_id: int
    name: str
