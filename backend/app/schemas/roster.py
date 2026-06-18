from datetime import datetime

from pydantic import BaseModel


class RosterStatusOut(BaseModel):
    """The corp roster's sync state, shown on the Managers page."""

    synced: bool
    synced_at: datetime | None = None
    member_count: int = 0


class RosterSyncResponse(BaseModel):
    """The SSO URL the SPA redirects to, to sync the corp roster."""

    authorization_url: str
    state: str


class RosterSyncRequest(BaseModel):
    """The OAuth callback payload completing a roster sync."""

    code: str
    state: str


class CorpMemberOut(BaseModel):
    """A corp member matched by the manager-designation search."""

    character_id: int
    name: str
