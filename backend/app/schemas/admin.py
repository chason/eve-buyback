"""API DTOs for the app-admin surface (ADR-0041/0042)."""

from datetime import datetime

from pydantic import BaseModel

from app.domain.entitlements import EntitlementSource


class CorpAccessOut(BaseModel):
    """One corp's access to the accounting add-on, as shown on the admin page."""

    corporation_id: int
    corporation_name: str
    active: bool
    # How the current grant came to be: admin action or matched ISK payment (ADR-0042);
    # null when the corp has never been granted access.
    source: EntitlementSource | None = None
    granted_at: datetime | None = None
    # null = access never expires (a perpetual grant).
    expires_at: datetime | None = None
    granted_by_character_id: int | None = None


class AccessGrantRequest(BaseModel):
    """Grant/extend a corp's access. Omitted/null `expires_at` = perpetual."""

    expires_at: datetime | None = None
