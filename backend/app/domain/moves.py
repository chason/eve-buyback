"""Pure rules for hangar move detection (ADR-0049, #200/#206). No I/O: the
reconciliation use case computes the per-(location, type) deltas and the
sell-side evidence and feeds the shortfall/excess pattern here; this proposes
"looks like a move" pairs.

Suggest-only by design: the caller books the ADR-0044 defaults regardless (the
shortfall flag and the deemed-cost excess lot) — a pair is only ever a decoration
on those artifacts, never a replacement. A shortfall + excess pattern is merely
*consistent with* a move; a human confirms (#201)."""

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
    `destination`. `qty` is the overlap — min(shortfall, total excess); any
    residual on either side keeps the default treatment.

    The excess end is the sum of disjoint evidence signals (ADR-0049 sell-side
    amendment): `qty_counted` units physically counted in a hangar beyond the
    books (the ADR-0044 deemed-lot excess) and `qty_listed` units sitting in the
    division's sell-order escrow beyond what idle lots at that location explain.
    Counted vs escrowed never overlap, so the portions sum without double
    counting; `qty == qty_counted + qty_listed` always."""

    type_id: int
    origin_location_id: str  # where the shortfall is
    destination_location_id: str  # where the excess evidence is
    qty: int
    qty_counted: int = 0
    qty_listed: int = 0


def _signal_totals(
    rows: list[tuple[str, int, int]], index: int, totals: dict
) -> None:
    """Fold one signal's `(location, type, qty)` rows into the per-(type,
    location) totals, at the signal's slot in the [counted, listed] list."""
    for location_id, type_id, qty in rows:
        if qty <= 0:
            continue
        slot = totals.setdefault(type_id, {}).setdefault(location_id, [0, 0])
        slot[index] += qty


def match_move_pairs(
    shortfalls: list[tuple[str, int, int]],
    excesses: list[tuple[str, int, int]],
    listed: list[tuple[str, int, int]] = (),
) -> list[MovePair]:
    """Pair same-type shortfalls against excess evidence (all sides are
    `(location, type, qty)`), quantity capped at the smaller side. Same type
    only, ever — quantity coincidence across *different* types is numerology
    (ADR-0049).

    `excesses` is hangar-counted excess (configured hangars, ADR-0044);
    `listed` is the division's unexplained sell-order escrow — any station it
    trades at, deliberately not just configured hangars (#206). The signals are
    disjoint (counted vs escrowed), so a destination's total excess is their
    sum; when the shortfall caps the pair below that total, the counted portion
    is filled first (it has a deemed lot on the books to reverse — the most
    valuable conversion), then listed.

    A type whose evidence sits at ONE location pairs against every shortfall
    location it has (#203): a single shortfall is the unambiguous pair (#200),
    and both signals fill its portions; several shortfalls are candidate
    origins — one pair per origin, each capped by its own shortfall, all
    pointing at the same evidence. Candidate pairs carry the COUNTED portion
    only: the shared deemed lot is the claim that confirming one candidate
    arbitrates (`_withdraw_claimed_siblings`), and the listed signal has no
    such claim — several candidates each carrying it could all convert the
    same escrowed units. The caller persists candidates as siblings and a
    manager picks; nothing is auto-chosen. Evidence at a shortfall location
    itself can't be the other end of a move and never anchors; a type with
    evidence at SEVERAL locations still proposes nothing — the destination
    itself is in doubt. Deterministic order (by type, then origin) so logs
    and tests read stably."""
    shorts_by_type: dict[int, list[tuple[str, int]]] = {}
    for location_id, type_id, qty in shortfalls:
        shorts_by_type.setdefault(type_id, []).append((location_id, qty))
    # type → location → [counted, listed]
    excess_totals: dict[int, dict[str, list[int]]] = {}
    _signal_totals(excesses, 0, excess_totals)
    _signal_totals(list(listed), 1, excess_totals)

    pairs: list[MovePair] = []
    for type_id in sorted(shorts_by_type.keys() & excess_totals.keys()):
        shorts = shorts_by_type[type_id]
        origins = {loc for loc, _ in shorts}
        # Evidence at a shortfall location itself can't be the other end of a
        # move (only the listed signal can land there — counted excess and a
        # shortfall are disjoint by construction).
        destinations = {
            loc: sig
            for loc, sig in excess_totals[type_id].items()
            if loc not in origins
        }
        if len(destinations) != 1:
            continue  # no destination, or the destination itself is in doubt
        ((destination, (counted, listed_qty)),) = destinations.items()
        if len(shorts) == 1:
            ((origin, short_qty),) = shorts
            qty = min(short_qty, counted + listed_qty)
            qty_counted = min(counted, qty)
            pairs.append(
                MovePair(
                    type_id=type_id,
                    origin_location_id=origin,
                    destination_location_id=destination,
                    qty=qty,
                    qty_counted=qty_counted,
                    qty_listed=qty - qty_counted,
                )
            )
            continue
        if counted <= 0:
            continue  # pure sell-side evidence never fans out to candidates
        for origin, short_qty in sorted(shorts):
            pairs.append(
                MovePair(
                    type_id=type_id,
                    origin_location_id=origin,
                    destination_location_id=destination,
                    qty=min(short_qty, counted),
                    qty_counted=min(short_qty, counted),
                )
            )
    return pairs


def unexplained_listed(
    listed: dict[tuple[str, int], int], idle: dict[tuple[str, int], int]
) -> list[tuple[str, int, int]]:
    """The division's sell-order escrow beyond what the ledger's idle lots at
    that location explain (#206), per `(location, type)`: `listed − idle`,
    floored at zero. Escrow fully covered by idle lots is the normal listed
    state (ADR-0044's offset accounts for it) — only the surplus is evidence
    that stock arrived off the books. Deterministic order for stable logs."""
    out: list[tuple[str, int, int]] = []
    for (location_id, type_id), qty in sorted(listed.items()):
        surplus = qty - idle.get((location_id, type_id), 0)
        if surplus > 0:
            out.append((location_id, type_id, surplus))
    return out
