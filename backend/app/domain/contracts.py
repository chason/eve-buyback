"""Pure contract-matching rules for the contract watcher (ADR-0037). No I/O: the use case
gathers the corp's ESI contracts + the appraisal facts and feeds them here as plain data
(`ContractObservation` / `AppraisalFacts`); these functions decide each appraisal's status.
Keeping the matching algorithm pure makes it testable without mocks."""

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

# The status surfaced on an appraisal. `mismatch` is set here (not derivable from ESI alone)
# when a contract cites the appraisal but its items/price/location differ.
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

# Lifecycle statuses worth item-validating (a live or accepted contract); voided ones are
# surfaced as-is without an items fetch.
VALIDATABLE_STATUSES: frozenset[ContractStatus] = frozenset({"in_progress", "completed"})

# Maximal runs of the appraisal public-id alphabet (base64url) in a contract title.
_ID_RUN = re.compile(r"[A-Za-z0-9_-]+")
_PUBLIC_ID_LEN = 12  # generate_appraisal_id() → secrets.token_urlsafe(9)

# When several contracts match one appraisal, prefer the most meaningful (lower wins).
_PRIORITY: dict[ContractStatus, int] = {
    "in_progress": 0,
    "completed": 1,
    "mismatch": 2,
    "rejected": 3,
    "cancelled": 3,
    "expired": 3,
    "failed": 3,
}


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


def match_appraisal_id(
    title: str | None, id_map: Mapping[str, uuid.UUID]
) -> uuid.UUID | None:
    """The appraisal whose 12-char public_id appears in the contract title (case-sensitive,
    exact). Checks each base64url run and any 12-char window inside a longer run."""
    if not title:
        return None
    for run in _ID_RUN.findall(title):
        if run in id_map:
            return id_map[run]
        for i in range(len(run) - _PUBLIC_ID_LEN + 1):
            window = run[i : i + _PUBLIC_ID_LEN]
            if window in id_map:
                return id_map[window]
    return None


@dataclass(frozen=True)
class ContractLink:
    """The single desired appraisal↔contract link the resolver computes for one appraisal,
    handed to `appraisal_contracts.reconcile_for_corp`."""

    appraisal_id: uuid.UUID
    contract_id: int
    status: ContractStatus
    issued_at: datetime
    completed_at: datetime | None = None


@dataclass(frozen=True)
class ContractObservation:
    """One contract that cites an appraisal, reduced to the plain facts the resolver needs.
    The application maps the ESI contract (+ its items) into this so the resolver stays pure;
    `items` is the included items summed by type id, empty for voided contracts (not fetched)."""

    appraisal_id: uuid.UUID
    contract_id: int
    lifecycle: ContractStatus
    issued_at: datetime
    completed_at: datetime | None
    price: Decimal
    start_location_id: int | None
    items: dict[int, int]


@dataclass(frozen=True)
class AppraisalFacts:
    """What a contract must match to be confirmed for an appraisal (ADR-0037): the accepted
    price, the delivery location, and the accepted line items."""

    accepted_total: Decimal
    delivery_location_id: str | None
    accepted_items: dict[int, int]


def resolve_best_links(
    observations: Sequence[ContractObservation],
    facts: Mapping[uuid.UUID, AppraisalFacts],
) -> dict[uuid.UUID, ContractLink]:
    """Reduce all cited contracts to one best link per appraisal: validate live/accepted
    contracts (→ the lifecycle status if they match the appraisal facts, else `mismatch`),
    surface voided ones as-is, and when several cite one appraisal keep the most meaningful
    (priority `in_progress > completed > mismatch > void`, newest issued as tiebreak)."""
    best: dict[uuid.UUID, ContractLink] = {}
    for obs in observations:
        if obs.lifecycle in VALIDATABLE_STATUSES:
            fact = facts.get(obs.appraisal_id)
            ok = fact is not None and contract_matches(
                price=obs.price,
                start_location_id=obs.start_location_id,
                items=obs.items,
                accepted_total=fact.accepted_total,
                delivery_location_id=fact.delivery_location_id,
                accepted_items=fact.accepted_items,
            )
            status: ContractStatus = obs.lifecycle if ok else "mismatch"
        else:
            status = obs.lifecycle

        link = ContractLink(
            appraisal_id=obs.appraisal_id,
            contract_id=obs.contract_id,
            status=status,
            issued_at=obs.issued_at,
            completed_at=obs.completed_at,
        )
        cur = best.get(obs.appraisal_id)
        if cur is None or _is_better(link, cur):
            best[obs.appraisal_id] = link
    return best


def _is_better(a: ContractLink, b: ContractLink) -> bool:
    """Prefer the more meaningful status; tiebreak the more recently issued contract."""
    pa, pb = _PRIORITY[a.status], _PRIORITY[b.status]
    if pa != pb:
        return pa < pb
    return a.issued_at > b.issued_at
