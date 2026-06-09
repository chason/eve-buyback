from datetime import datetime

from pydantic import BaseModel


class StructureAuthorizeResponse(BaseModel):
    """The SSO URL the SPA redirects to, to grant structure-market access."""

    authorization_url: str
    state: str


class StructureAuthorizeRequest(BaseModel):
    """The OAuth callback payload completing the structure-access grant."""

    code: str
    state: str


class StructureTokenStatus(BaseModel):
    """The corp's structure-market authorization status (never the token itself)."""

    authorized: bool
    character_name: str | None = None
    scopes: str | None = None
    # True when the last refresh failed (revoked grant / lost docking) → re-authorize.
    expired: bool = False
    created_at: datetime | None = None


class StructureSearchResult(BaseModel):
    """A structure the authorizing character can access, matched by name."""

    structure_id: str
    name: str
