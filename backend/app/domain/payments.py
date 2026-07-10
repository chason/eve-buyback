"""Pure rules for ISK access-payment reconciliation (ADR-0042). No I/O: the use case
fetches the operator's wallet journal and feeds plain values here; these functions
decide what counts as a payment, which corp it references, and how far it extends
access. Money is Decimal (ADR-0020)."""

import re
from datetime import datetime, timedelta
from decimal import Decimal

# The reference a corp includes in the ISK transfer reason at checkout: "BB-<corp eve
# id>". Deterministic from the corp id — nothing to persist, nothing to look up.
_REFERENCE = re.compile(r"\bBB-(\d{1,20})\b", re.IGNORECASE)

# Journal ref_types that carry an ISK transfer *to* the operator: a character sending
# ISK directly, or a corp wallet paying a character.
_PAYMENT_REF_TYPES = frozenset({"player_donation", "corporation_account_withdrawal"})


def payment_reference(corporation_eve_id: int) -> str:
    """The reference a corp puts in the transfer reason so reconciliation can match
    the payment to them."""
    return f"BB-{corporation_eve_id}"


def parse_payment_reference(reason: str | None) -> int | None:
    """The corp EVE id referenced in a transfer reason, or None. EVE prefixes player
    donation reasons with boilerplate, so the reference is searched, not matched
    exactly; the first occurrence wins."""
    if not reason:
        return None
    m = _REFERENCE.search(reason)
    return int(m.group(1)) if m else None


def is_incoming_payment(
    *, ref_type: str, amount: Decimal | None, second_party_id: int | None,
    operator_character_id: int,
) -> bool:
    """Whether a journal entry is ISK arriving at the operator's wallet — a positive
    transfer of a payment ref_type addressed to the operator. Everything else in the
    journal (market activity, taxes, the operator's own spending) is ignored."""
    return (
        ref_type in _PAYMENT_REF_TYPES
        and amount is not None
        and amount > 0
        and second_party_id == operator_character_id
    )


def periods_for(amount: Decimal, price_isk: int) -> int:
    """How many whole access periods an amount buys (floor). 0 when the amount is
    below the price — such a payment is recorded but never auto-extends access."""
    if price_isk <= 0:
        return 0
    return int(amount // Decimal(price_isk))


def extend_expiry(
    current_expires_at: datetime | None, *, now: datetime, periods: int, period_days: int
) -> datetime:
    """The new expiry after applying `periods`: stacked onto the remaining time when
    access is still active, or restarted from now when it lapsed (or never existed).
    A perpetual grant never reaches here — reconciliation leaves NULL expiries alone."""
    base = current_expires_at if current_expires_at and current_expires_at > now else now
    return base + timedelta(days=period_days * periods)
