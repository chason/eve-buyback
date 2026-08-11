"""Pure rules for declared shipments (ADR-0049, #208). No I/O: a manager records
a haul before it happens (stock → in transit → destination), which keeps
`qty_idle` honest at both ends so the reconciliation never flags it — the
declared move preempts the heuristic instead of racing it.

The shortfall side is arithmetic on `expected` (an open shipment's units are
treated as having left the origin, exactly like sell-order escrow —
`domain/lots.qty_idle`); this module owns the excess side, where the physical
goods sit at ONE end of the haul the whole time it is open: still at the origin
before the freighter picks them up, or already at the destination before anyone
clicks "arrived"."""

from typing import Literal

from app.domain.reconciliation import Delta

# Lifecycle of a declared haul: open (on the road — the in-transit allocation,
# ADR-0043) until a manager marks it arrived. No failure state: a haul that
# never lands just stays open until a human resolves it one way or the other.
ShipmentStatus = Literal["open", "arrived"]


def absorb_open_shipments(
    deltas: list[Delta], covered: dict[tuple[str, int], int]
) -> list[Delta]:
    """Shrink excess by what open shipments already explain, per slot.

    `covered` is the open-shipment quantity spoken for at each `(location,
    type)` — at the ORIGIN (recorded but not yet picked up: the in-transit
    subtraction lowered `expected`, so the still-sitting stock would read as
    excess) and at the DESTINATION (landed but not yet marked arrived: off-app
    excess a deemed-cost lot would double-count once the arrival relocates the
    real lots). Either way the excess could also feed a move pair the manager
    already preempted. Only EXCESS shrinks — nothing here can invent or deepen
    a shortfall — and residual excess beyond the covered quantity keeps the
    ADR-0044 default treatment."""
    out: list[Delta] = []
    for delta in deltas:
        if delta.kind != "excess":
            out.append(delta)
            continue
        spoken_for = covered.get((delta.location_id, delta.type_id), 0)
        if spoken_for <= 0:
            out.append(delta)
            continue
        remainder = delta.qty - spoken_for
        if remainder > 0:
            out.append(
                Delta(
                    location_id=delta.location_id,
                    type_id=delta.type_id,
                    kind="excess",
                    qty=remainder,
                )
            )
    return out
