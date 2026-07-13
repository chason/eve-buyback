"""Pure rules for the hangar reconciliation (ADR-0044). No I/O: the use case fetches
the physical hangar counts and the ledger's idle stock and feeds them here; this
decides what differs. What to DO about a difference (deemed-cost lot, flag, log) is
the use case's business — this only measures."""

from dataclasses import dataclass
from typing import Literal

# What a sync observed for one (location, type): more in the hangar than the books
# expect (off-app buyback / opening stock), or less (an unrecorded sale or move).
ReconciliationKind = Literal["excess", "shortfall"]


@dataclass(frozen=True)
class Delta:
    """One (location, type) whose physical count differs from the books. `qty` is
    the magnitude (always positive); `kind` carries the direction."""

    location_id: str
    type_id: int
    kind: ReconciliationKind
    qty: int


def reconcile(
    counted: dict[tuple[str, int], int], expected: dict[tuple[str, int], int]
) -> list[Delta]:
    """Diff the physical hangar count against the ledger's expected idle stock, per
    `(location_id, type_id)` (ADR-0044). Matched stock produces nothing — it keeps
    its recorded cost untouched. Deterministic order (by location, then type) so
    logs and tests read stably."""
    deltas: list[Delta] = []
    for slot in sorted(counted.keys() | expected.keys()):
        diff = counted.get(slot, 0) - expected.get(slot, 0)
        if diff == 0:
            continue
        location_id, type_id = slot
        deltas.append(
            Delta(
                location_id=location_id,
                type_id=type_id,
                kind="excess" if diff > 0 else "shortfall",
                qty=abs(diff),
            )
        )
    return deltas
