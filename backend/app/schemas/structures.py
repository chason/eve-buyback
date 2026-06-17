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

    # Whether this server can use structure markets at all: False while the
    # token-encryption key is the public placeholder (ADR-0029). The UI disables
    # the structure options entirely when this is False.
    configured: bool = True
    authorized: bool
    character_name: str | None = None
    scopes: str | None = None
    # True when the last refresh failed (revoked grant / lost docking) → re-authorize.
    expired: bool = False
    # When that failure was first recorded, so the UI can say "failing since …" (#68).
    failed_since: datetime | None = None
    created_at: datetime | None = None
    # Set only on the completion response when a re-authorization switched the
    # authorizing character (EVE can't pin the picker) — the *previous* character's
    # name, so the UI can warn that the structure token now belongs to someone else.
    replaced_character_name: str | None = None


class StructureSearchResult(BaseModel):
    """A structure the authorizing character can access, matched by name."""

    structure_id: str
    name: str
