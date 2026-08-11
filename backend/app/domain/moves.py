"""Pure rules for hangar move detection (ADR-0049, #200). No I/O: the
reconciliation use case computes the per-(location, type) deltas and feeds the
same-sync shortfall/excess pattern here; this proposes "looks like a move" pairs.

Suggest-only by design: the caller books the ADR-0044 defaults regardless (the
shortfall flag and the deemed-cost excess lot) — a pair is only ever a decoration
on those artifacts, never a replacement. A shortfall + excess pattern is merely
*consistent with* a move; a human confirms (in a later slice)."""

from dataclasses import dataclass
from typing import Literal

# Lifecycle of a persisted suggestion. `dismissed` is a human's "not a move"
# (#202) — it stands as a decision and suppresses re-suggesting the same
# pattern; `withdrawn` is bookkeeping (#202) — a later sync invalidated the
# pair (the excess evaporated, or the shortfall flag resolved some other way),
# no human decided anything, and a re-materialized pair may suggest again.
# `confirmed` is #201's converting action.
MoveSuggestionStatus = Literal["pending", "confirmed", "dismissed", "withdrawn"]


@dataclass(frozen=True)
class MovePair:
    """One proposed move: the same type short at `origin` and over at
    `destination` in the same sync. `qty` is the overlap — min(shortfall,
    excess); any residual on either side keeps the default treatment."""

    type_id: int
    origin_location_id: str  # where the shortfall is
    destination_location_id: str  # where the excess is
    qty: int


def match_move_pairs(
    shortfalls: list[tuple[str, int, int]],
    excesses: list[tuple[str, int, int]],
) -> list[MovePair]:
    """Pair same-type shortfalls against excesses across hangars (both sides are
    `(location, type, qty)`), quantity capped at the smaller side. Same type
    only, ever — quantity coincidence across *different* types is numerology
    (ADR-0049).

    A type with ONE excess location pairs against every shortfall location it
    has (#203): a single shortfall is the unambiguous pair (#200); several are
    candidate origins — one pair per origin, each capped by its own shortfall,
    all pointing at the same excess. The caller persists them as siblings and a
    manager picks which one to confirm; nothing is auto-chosen. A type over at
    SEVERAL locations still proposes nothing: with the destination itself in
    doubt there is no single found-stock lot for the candidates to share, so
    the defaults stand until a human sorts it out. Deterministic order (by
    type, then origin) so logs and tests read stably."""
    shorts_by_type: dict[int, list[tuple[str, int]]] = {}
    for location_id, type_id, qty in shortfalls:
        shorts_by_type.setdefault(type_id, []).append((location_id, qty))
    excess_by_type: dict[int, list[tuple[str, int]]] = {}
    for location_id, type_id, qty in excesses:
        excess_by_type.setdefault(type_id, []).append((location_id, qty))

    pairs: list[MovePair] = []
    for type_id in sorted(shorts_by_type.keys() & excess_by_type.keys()):
        overs = excess_by_type[type_id]
        if len(overs) != 1:
            continue  # ambiguous destination — nothing to anchor a pair on
        (destination, excess_qty) = overs[0]
        for origin, short_qty in sorted(shorts_by_type[type_id]):
            pairs.append(
                MovePair(
                    type_id=type_id,
                    origin_location_id=origin,
                    destination_location_id=destination,
                    qty=min(short_qty, excess_qty),
                )
            )
    return pairs
