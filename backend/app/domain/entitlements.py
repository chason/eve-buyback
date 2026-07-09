"""Domain rules for per-corp feature entitlements (ADR-0042).

An entitlement is the data-level gate on a paid add-on: a corp either holds an active
entitlement row for a feature or it doesn't. The predicate here is the single definition
of "active"; use cases call it rather than comparing timestamps themselves.
"""

from datetime import datetime
from typing import Literal

# The gated features. One value today; the accounting add-on's reports/ledger all gate
# on "accounting". New paid features add a Literal member (single source of truth for
# the CHECK constraint, ADR-0021).
Feature = Literal["accounting"]

# How a grant came to be (ADR-0042): extended automatically by matched ISK payment, or
# set by an app admin (which is also how self-hosters enable the feature for their own
# corp — there is deliberately no separate "config" source).
EntitlementSource = Literal["payment", "admin"]


def entitlement_active(expires_at: datetime | None, now: datetime) -> bool:
    """Whether an entitlement is active (ADR-0042): a NULL expiry is a perpetual grant;
    otherwise it must expire in the future."""
    return expires_at is None or expires_at > now
