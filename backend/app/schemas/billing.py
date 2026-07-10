"""API DTOs for the accounting-access checkout view (ADR-0042)."""

from datetime import datetime

from pydantic import BaseModel


class AccountingAccessOut(BaseModel):
    """A corp's accounting-access status plus how to pay (manager-visible). When no
    operator wallet is connected (`payment_configured` False), the UI shows status
    only — there is nowhere to send ISK yet."""

    active: bool
    expires_at: datetime | None = None
    price_isk: int
    period_days: int
    reference: str
    payment_configured: bool
    operator_character_name: str | None = None
