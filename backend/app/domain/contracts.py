"""Pure contract-matching rules for the contract watcher (ADR-0037). No I/O — the use
case feeds these the contract + appraisal facts it gathered from ESI and the DB."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

# The status surfaced on an appraisal. `mismatch` is set by the use case (not derivable
# from ESI alone) when a contract cites the appraisal but its items/price/location differ.
ContractStatus = Literal[
    "in_progress",
    "completed",
    "rejected",
    "cancelled",
    "expired",
    "failed",
    "mismatch",
]

# ESI lifecycle statuses that mean "the deal went through".
_COMPLETED = frozenset({"finished", "finished_issuer", "finished_contractor"})
# ESI statuses that void the contract (surfaced as-is so a manager sees what happened).
_VOIDED = frozenset({"cancelled", "rejected", "failed"})
# ESI statuses meaning the contract is gone entirely — drop the link.
_GONE = frozenset({"deleted", "reversed"})


def derive_lifecycle_status(
    esi_status: str, *, date_expired: datetime | None, now: datetime
) -> ContractStatus | None:
    """Map an ESI contract `status` to the appraisal-facing lifecycle status, or None to
    drop the link. ESI has no `expired` status, so an still-`outstanding` contract past
    its `date_expired` is derived as `expired`."""
    if esi_status in _COMPLETED:
        return "completed"
    if esi_status in _VOIDED:
        return esi_status  # type: ignore[return-value]  # one of cancelled/rejected/failed
    if esi_status in _GONE:
        return None
    if esi_status in ("outstanding", "in_progress"):
        if date_expired is not None and date_expired < now:
            return "expired"
        return "in_progress"
    return None  # unknown status → don't track


def contract_matches(
    *,
    price: Decimal,
    start_location_id: int | None,
    items: dict[int, int],
    accepted_total: Decimal,
    delivery_location_id: str | None,
    accepted_items: dict[int, int],
) -> bool:
    """Whether a contract genuinely corresponds to the appraisal: the price equals the
    accepted total, it's at the appraisal's delivery location, and its items **exactly**
    equal the appraisal's accepted lines (same type ids, same quantities, no extras)."""
    if price != accepted_total:
        return False
    if delivery_location_id is None or start_location_id is None:
        return False  # can't validate the location → not a confirmed match
    if str(start_location_id) != str(delivery_location_id):
        return False
    return items == accepted_items


@dataclass(frozen=True)
class ContractLink:
    """The single desired appraisal↔contract link the use case computes for one appraisal,
    handed to `appraisal_contracts.reconcile_for_corp`."""

    appraisal_id: uuid.UUID
    contract_id: int
    status: ContractStatus
    issued_at: datetime
    completed_at: datetime | None = None
