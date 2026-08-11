"""Pure rules for hangar move detection (ADR-0049, #200/#206). No I/O: the
reconciliation use case computes the per-(location, type) deltas and the
sell-side evidence and feeds the shortfall/excess pattern here; this proposes
"looks like a move" pairs.

Suggest-only by design: the caller books the ADR-0044 defaults regardless (the
shortfall flag and the deemed-cost excess lot) — a pair is only ever a decoration
on those artifacts, never a replacement. A shortfall + excess pattern is merely
*consistent with* a move; a human confirms (#201)."""

from dataclasses import dataclass
from decimal import Decimal
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
    books (the ADR-0044 deemed-lot excess), `qty_listed` units sitting in the
    division's sell-order escrow beyond what idle lots at that location
    explain, and `qty_sold` units already sold there at an estimated cost
    (#207 — the ADR-0045 no-lot fallback and consumed deemed lots, less what
    prior confirmations retired). Counted vs escrowed vs sold never overlap,
    so the portions sum without double counting;
    `qty == qty_counted + qty_listed + qty_sold` always."""

    type_id: int
    origin_location_id: str  # where the shortfall is
    destination_location_id: str  # where the excess evidence is
    qty: int
    qty_counted: int = 0
    qty_listed: int = 0
    qty_sold: int = 0


def _signal_totals(
    rows: list[tuple[str, int, int]], index: int, totals: dict
) -> None:
    """Fold one signal's `(location, type, qty)` rows into the per-(type,
    location) totals, at the signal's slot in the [counted, listed, sold]
    list."""
    for location_id, type_id, qty in rows:
        if qty <= 0:
            continue
        slot = totals.setdefault(type_id, {}).setdefault(location_id, [0, 0, 0])
        slot[index] += qty


def match_move_pairs(
    shortfalls: list[tuple[str, int, int]],
    excesses: list[tuple[str, int, int]],
    listed: list[tuple[str, int, int]] = (),
    sold: list[tuple[str, int, int]] = (),
) -> list[MovePair]:
    """Pair same-type shortfalls against excess evidence (all sides are
    `(location, type, qty)`), quantity capped at the smaller side. Same type
    only, ever — quantity coincidence across *different* types is numerology
    (ADR-0049).

    `excesses` is hangar-counted excess (configured hangars, ADR-0044);
    `listed` is the division's unexplained sell-order escrow and `sold` its
    unretired estimated-cost sales — both at any station it trades at,
    deliberately not just configured hangars (#206/#207). The signals are
    disjoint (counted vs escrowed vs sold), so a destination's total excess is
    their sum; when the shortfall caps the pair below that total, the counted
    portion is filled first (it has a deemed lot on the books to reverse — the
    most valuable conversion), then listed (future fills then sell at verified
    cost), then sold (only the aggregate true-up is left to gain).

    A type whose evidence sits at ONE location pairs against every shortfall
    location it has (#203): a single shortfall is the unambiguous pair (#200),
    and every signal fills its portions; several shortfalls are candidate
    origins — one pair per origin, each capped by its own shortfall, all
    pointing at the same evidence. Candidate pairs carry the COUNTED portion
    only: the shared deemed lot is the claim that confirming one candidate
    arbitrates (`_withdraw_claimed_siblings`), and the sell-side signals have
    no such claim — several candidates each carrying them could all convert
    the same escrowed or sold units. The caller persists candidates as
    siblings and a manager picks; nothing is auto-chosen. Evidence at a shortfall location
    itself can't be the other end of a move and never anchors; a type with
    evidence at SEVERAL locations still proposes nothing — the destination
    itself is in doubt. Deterministic order (by type, then origin) so logs
    and tests read stably."""
    shorts_by_type: dict[int, list[tuple[str, int]]] = {}
    for location_id, type_id, qty in shortfalls:
        shorts_by_type.setdefault(type_id, []).append((location_id, qty))
    # type → location → [counted, listed, sold]
    excess_totals: dict[int, dict[str, list[int]]] = {}
    _signal_totals(excesses, 0, excess_totals)
    _signal_totals(list(listed), 1, excess_totals)
    _signal_totals(list(sold), 2, excess_totals)

    pairs: list[MovePair] = []
    for type_id in sorted(shorts_by_type.keys() & excess_totals.keys()):
        shorts = shorts_by_type[type_id]
        origins = {loc for loc, _ in shorts}
        # Evidence at a shortfall location itself can't be the other end of a
        # move (only the sell-side signals can land there — counted excess and
        # a shortfall are disjoint by construction).
        destinations = {
            loc: sig
            for loc, sig in excess_totals[type_id].items()
            if loc not in origins
        }
        if len(destinations) != 1:
            continue  # no destination, or the destination itself is in doubt
        ((destination, (counted, listed_qty, sold_qty)),) = destinations.items()
        if len(shorts) == 1:
            ((origin, short_qty),) = shorts
            qty = min(short_qty, counted + listed_qty + sold_qty)
            qty_counted = min(counted, qty)
            qty_listed = min(listed_qty, qty - qty_counted)
            pairs.append(
                MovePair(
                    type_id=type_id,
                    origin_location_id=origin,
                    destination_location_id=destination,
                    qty=qty,
                    qty_counted=qty_counted,
                    qty_listed=qty_listed,
                    qty_sold=qty - qty_counted - qty_listed,
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


def unretired_sold(
    sold: dict[tuple[str, int], int], retired: dict[tuple[str, int], int]
) -> list[tuple[str, int, int]]:
    """The division's estimated-cost sales not yet retired by a confirmed move
    (#207), per `(location, type)`: `sold − retired`, floored at zero. Sale
    rows are frozen facts and can't carry the state themselves, so retirement
    is tracked on the confirmed suggestions — quantity a prior confirmation
    already trued-up drops out of future pairings (ADR-0049). Deterministic
    order for stable logs."""
    out: list[tuple[str, int, int]] = []
    for (location_id, type_id), qty in sorted(sold.items()):
        surplus = qty - retired.get((location_id, type_id), 0)
        if surplus > 0:
            out.append((location_id, type_id, surplus))
    return out


def sold_cost_estimate(
    rows: list[tuple[int, Decimal]], *, skip: int, take: int
) -> Decimal:
    """The estimated COGS that `take` units of already-sold stock recognized
    (#207): walk the location's estimated-cost sale rows oldest first — the
    same order retirement consumes origin lots — skipping the `skip` units a
    prior confirmation already trued-up. The true-up books the difference
    between the retired lots' real landed cost and this sum; the sale rows
    themselves stay frozen (ADR-0043 #159)."""
    total = Decimal(0)
    to_skip, to_take = skip, take
    for qty, unit_cost in rows:
        if to_take <= 0:
            break
        skipped = min(to_skip, qty)
        to_skip -= skipped
        counted = min(qty - skipped, to_take)
        total += counted * unit_cost
        to_take -= counted
    return total
