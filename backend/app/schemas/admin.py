"""API DTOs for the app-admin surface (ADR-0041/0042)."""

import uuid
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


class OperatorWalletStatus(BaseModel):
    """The operator wallet connection (ADR-0042), as shown on the admin page."""

    configured: bool  # a real token-encryption key is set (ADR-0029)
    connected: bool
    character_name: str | None = None
    expired: bool = False
    created_at: datetime | None = None


class WalletAuthorizeResponse(BaseModel):
    authorization_url: str
    state: str


class WalletAuthorizeRequest(BaseModel):
    code: str
    state: str


class PaymentOut(BaseModel):
    """One incoming ISK transfer seen in the operator's wallet (ADR-0042)."""

    id: uuid.UUID
    amount: str  # Decimal ISK as a string (ADR-0020)
    sender_name: str | None = None
    reason: str | None = None
    received_at: datetime
    matched: bool
    matched_corporation_name: str | None = None
    periods_granted: int = 0


class PaymentMatchRequest(BaseModel):
    """Apply an unmatched payment to a corporation (by EVE corp id)."""

    corporation_id: int
