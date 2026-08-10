# 0049. Move detection: pairing hangar shortfalls with excess across stations

- **Status:** Proposed
- **Date:** 2026-08-11
- **Relates to:** [ADR-0044](0044-hangar-inventory-reconciliation.md) (the reconciliation this
  refines; its shortfall flags + deemed-cost excess lots are the inputs),
  [ADR-0043](0043-lot-based-buyback-accounting.md) (lot location, `qty_idle`, FIFO, aging),
  [ADR-0045](0045-esi-sales-ingestion-and-manual-entry.md) (reversing-entry corrections, the
  shared "Needs a look" log, freight as a selling expense),
  [ADR-0047](0047-lot-transformations-reprocessing.md) (lot splits that carry cost into child
  lots — the same mechanic a partial move uses),
  [ADR-0042](0042-paid-accounting-entitlements.md) (the gate)

## Context

The reconciliation (ADR-0044) is keyed by `(location, type)` and has no item identity — EVE
`item_id`s don't survive stacking/repackaging, so it can't have one. When stock is hauled from
buyback hangar A to buyback hangar B without being recorded, one physical move therefore produces
**two unrelated-looking discrepancies**: a shortfall at A (flagged for a human) and an excess at B
(auto-created as a **new deemed-cost lot**). The book forgets the real cost basis it already had,
replaces it with an estimate, and leaves a shortfall flag that the manager must resolve by hand —
for what was routine hangar logistics. Managers who consolidate stock regularly would drown the
"Needs a look" list in self-inflicted noise.

## Decision

**During each reconciliation, pair same-type shortfalls and excesses across configured hangars
into a "looks like a move" suggestion. The default treatment (flag + deemed-cost lot) still
applies immediately; confirming the suggestion converts it retroactively — reversing the deemed
lot and relocating the original lots with their cost basis, acquisition dates, and flags intact.**

- **The pairing heuristic (a pure `domain/` function).** After computing per-`(location, type)`
  deltas but before writing anything: for each type with a shortfall at one configured hangar and
  an excess at another — in the same sync **or** against a still-unresolved shortfall flag from a
  prior sync (the freighter may be mid-haul when a sync runs) — propose a move of
  `qty = min(shortfall, excess)`. Residual excess/shortfall beyond the paired quantity keeps the
  default ADR-0044 treatment. When several pairings are possible (same type short at two stations,
  over at one), the suggestion lists the candidates; the manager picks. Nothing is inferred from
  quantity coincidence alone across *different* types.
- **Suggest, never auto-apply.** A shortfall + excess pair is only *consistent with* a move — it
  could equally be a genuine loss at A coinciding with genuine off-app buyback at B. Auto-applying
  would silently launder a real loss into relocated stock and carry a cost basis to items that
  never had it. The suggestion is a **decoration on the reconciliation log**, not a new state: the
  deemed lot at B is still created (flagged estimated, per ADR-0044), the shortfall at A is still
  flagged — the book is never knowingly wrong while waiting, and there is no blocking queue
  (the exact rationale of ADR-0044's flag-plus-log decision).
- **Confirming = reversing entries, not edits (ADR-0045).** One click on "yes, this was a move"
  books, atomically: (1) a reversing entry that cancels the deemed-cost lot at B for the paired
  quantity; (2) a **relocation** of the oldest idle lots of that type at A — FIFO, consistent with
  every other cost-flow assumption in the ledger — to location B, splitting a lot when the move is
  partial (child lot carries `unit_purchase_cost`, `acquired_at`, `cost_is_estimated`, and any
  `written_down_to` unchanged — the ADR-0047 split mechanic; aging and FIFO order are preserved
  because relocation is not acquisition); (3) resolution of the shortfall flag, with the whole
  conversion recorded in the log. Idempotent per suggestion; a suggestion invalidated by a later
  sync (the "excess" evaporated) is withdrawn, and one whose deemed lot has since been partially
  consumed by a sale converts only the unconsumed remainder (the consumed units keep their deemed
  COGS — sale rows are frozen facts, ADR-0043 #159).
- **Optional freight cost on confirm.** The confirmation form accepts an optional hauling cost,
  booked as a **selling expense attributed to the lots moved** (ADR-0043/0045: corp freight is a
  selling cost, never landed cost — relocation must not change a lot's carrying value).
- **Declared moves preempt the heuristic.** A manager can also record a move **proactively**
  ("shipment": lots → in-transit → destination), which keeps `qty_idle` honest at both ends so the
  reconciliation never flags it (ADR-0043's in-transit allocation). The heuristic exists for the
  moves nobody recorded — the override stays available, the automation absorbs the toil, same
  shape as ADR-0044's manual-entry relationship.
- **Plain-English UI (ADR-0043 constraint).** The suggestion reads "Looks like 40 Tritanium moved
  from Jita to Amarr — confirm?"; never "reconciliation pairing", "reversing entry", or "lot
  relocation".

## Consequences

- Routine hangar consolidation stops generating permanent noise: the two discrepancies collapse
  into one suggestion, and confirming it **restores the verified cost basis** instead of leaving
  an estimate in the book. Measured cost survives logistics.
- New pieces: a pairing pure function in `domain/`, a `confirm_move` use case (reversal + FIFO
  relocation + flag resolution in one unit of work), suggestion rows keyed to the flags they
  decorate, and log entries. No new ESI scopes — pairing runs on data the sync already fetches —
  so **no Privacy page change**. Gated behind the entitlement (ADR-0042).
- Wrong confirmations are recoverable: everything booked is a reversing entry away from undone
  (ADR-0045), and the log shows who confirmed what.
- Limits, accepted openly: pairing is per-type and quantity-overlap only, so a move that repackages
  into a *different* type (reprocessing in transit) is out of scope (that's ADR-0047's domain), and
  simultaneous loss-at-A + off-app-buyback-at-B of the same type can masquerade as a move — which
  is exactly why a human confirms.

## Alternatives considered

- **Auto-apply unambiguous pairs** — silently converts a possible real loss + real off-app buyback
  into a fake move, mis-carrying cost basis with no human in the loop; the failure mode is
  invisible precisely because the book looks clean afterwards. Rejected: suggest-only.
- **Track `item_id`s to detect moves definitively** — `item_id`s don't survive
  stacking/splitting/repackaging, which hauling does constantly; would give false negatives
  exactly when needed. Rejected (same reasoning as ADR-0044's count-not-ledger stance).
- **Hold the deemed lot / shortfall flag while a suggestion is pending** — reintroduces the
  blocking approval queue ADR-0044 explicitly rejected: an ignored suggestion would mean a
  knowingly-understated book. Rejected; default treatment applies immediately, confirmation
  converts retroactively.
- **Require pre-recorded shipments for every move (no heuristic)** — the disciplined path already
  exists and preempts flags, but mandating it is pure toil and one forgotten shipment poisons the
  log; heuristic + override mirrors ADR-0044's automation-absorbs-toil stance. Rejected as the
  *only* path.
- **Pair by quantity match across types or across corps' full asset tree** — quantity coincidence
  without type identity is numerology; scanning beyond configured hangars pairs against stock the
  ledger doesn't govern. Rejected; same-type, configured-hangars only.
