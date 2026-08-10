# 0049. Move detection: pairing hangar shortfalls with excess across stations

- **Status:** Proposed
- **Date:** 2026-08-11
- **Relates to:** [ADR-0044](0044-hangar-inventory-reconciliation.md) (the reconciliation this
  refines; its shortfall flags + deemed-cost excess lots are the inputs),
  [ADR-0043](0043-lot-based-buyback-accounting.md) (lot location, `qty_idle`, FIFO, aging),
  [ADR-0045](0045-esi-sales-ingestion-and-manual-entry.md) (reversing-entry corrections, the
  shared "Needs a look" log, freight as a selling expense, and the sell side this pairing now
  reads: the order snapshot and the no-lot sale fallback),
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

The commonest logistics loop is worse still. Stock is usually hauled to a trade hub **to be
sold**, and listed on the market shortly after arrival — and units in sell-order escrow are
invisible to hangar counts, so a sync at B sees no excess at all. "Buy at A, sell at hub B"
therefore produces a shortfall at A that hangar-excess pairing can never match, while every market
fill at B books the ADR-0045 no-lot fallback — a fully-consumed deemed-cost lot and an
`unmatched_sale` flag, fill by fill. The verified cost basis strands in idle lots at A, realized
profit is computed from estimates, and the log fills with two kinds of noise that are really one
routine haul.

## Decision

**During each reconciliation, pair same-type shortfalls and excesses across configured hangars —
and, on the sell side, escrowed listings and estimated-cost sales at the stations the buyback
wallet division trades at (ADR-0045) — into a "looks like a move" suggestion. The default
treatment (flag + deemed-cost lot) still applies immediately; confirming the suggestion converts
it retroactively — reversing the deemed lot and relocating the original lots with their cost
basis, acquisition dates, and flags intact.**

- **The pairing heuristic (a pure `domain/` function).** After computing per-`(location, type)`
  deltas but before writing anything: for each type with a shortfall at one configured hangar and
  an excess at another — in the same sync **or** against a still-unresolved shortfall flag from a
  prior sync (the freighter may be mid-haul when a sync runs) — propose a move of
  `qty = min(shortfall, excess)`. Residual excess/shortfall beyond the paired quantity keeps the
  default ADR-0044 treatment. When several pairings are possible (same type short at two stations,
  over at one), the suggestion lists the candidates; the manager picks. Nothing is inferred from
  quantity coincidence alone across *different* types.
- **Sell-side evidence pairs too (the haul-to-sell-hub case).** The excess end of a pair can also
  be, per `(location, type)`: **unexplained listed stock** — the division's sell-order snapshot
  quantity at a location beyond what the ledger's idle lots there explain — and **estimated-cost
  sold stock** — sale rows at that location still carrying `cost_is_estimated` (the ADR-0045
  no-lot fallback, and deemed hangar-excess lots consumed before anyone confirmed). The three
  signals are disjoint by construction (counted vs. escrowed vs. sold) and sum toward the same
  `min()` cap; quantity already retired by a prior confirmation drops out of future pairings. Both
  new signals are read from data the sales ingestion already fetches — no new ESI calls. Sell-side
  locations are deliberately **not** limited to configured hangars: a hangar *count* is only
  trusted where a hangar is configured, but the division's own orders and fills are
  ledger-governed facts wherever they occur — the wallet division, not the station list, scopes
  the sell side (ADR-0045).
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
  consumed — **sold or transformed** (ADR-0047) — converts only the unconsumed remainder
  (`qty_remaining`): sold units keep their frozen deemed COGS (sale rows are frozen facts,
  ADR-0043 #159), and transformed units' deemed cost has already flowed into their child lots and
  stays there, still flagged estimated — re-costing children retroactively would cascade into
  frozen sale facts wherever a child has since sold.
- **Confirming a sell-side pair converts by portion.** The **listed** (unsold) portion relocates
  real lots exactly as above — future fills then consume the verified cost, and ADR-0044's escrow
  offset accounts for the listed units, so the destination stays clean whether or not it is a
  configured hangar. The **already-sold** portion never touches the booked sale rows (the same
  frozen-facts stance as the partial-consumption clause above): instead the confirm **retires**
  the oldest idle lots of that type at the origin — FIFO, for the sold quantity — and books the
  difference between their real landed cost and the estimated COGS those sales recognized as one
  **cost true-up line** attributed to the move (positive or negative), logged like every other
  conversion. Total realized profit comes out exact; the per-sale rows keep `cost_is_estimated`
  so nobody mistakes them for measured. This also closes the loop the partial-consumption clause
  leaves open: the consumed remainder that used to strand real lots at the origin (re-flagged as
  a shortfall with nothing left to pair) now re-pairs as estimated-cost sold stock and retires
  the same way.
- **Optional freight cost on confirm.** The confirmation form accepts an optional hauling cost,
  booked as a **selling expense attributed to the lots moved** (ADR-0043/0045: corp freight is a
  selling cost, never landed cost — relocation must not change a lot's carrying value).
- **Declared moves preempt the heuristic.** A manager can also record a move **proactively**
  ("shipment": lots → in-transit → destination), which keeps `qty_idle` honest at both ends so the
  reconciliation never flags it (ADR-0043's in-transit allocation). The heuristic exists for the
  moves nobody recorded — the override stays available, the automation absorbs the toil, same
  shape as ADR-0044's manual-entry relationship.
- **Plain-English UI (ADR-0043 constraint).** The suggestion reads "Looks like 40 Tritanium moved
  from Amarr to Jita — confirm?" — or, sell-side, "Looks like 100 Tritanium moved from Amarr to
  Jita: 60 are listed for sale there and 40 already sold — confirm?" — never "reconciliation
  pairing", "reversing entry", "lot relocation", or "cost true-up". The true-up surfaces as
  "profit corrected by …".

## Consequences

- Routine hangar consolidation stops generating permanent noise: the two discrepancies collapse
  into one suggestion, and confirming it **restores the verified cost basis** instead of leaving
  an estimate in the book. Measured cost survives logistics.
- The haul-to-sell-hub loop — the commonest reason stock moves at all — now converges instead of
  accumulating `unmatched_sale` noise fill by fill: one confirmation relocates what is still
  listed (so the rest of the run sells at verified cost) and true-ups what already sold. Confirming
  *early* still matters: every fill before the confirm books estimated COGS that only the
  aggregate true-up corrects.
- New pieces: a pairing pure function in `domain/`, a `confirm_move` use case (reversal + FIFO
  relocation/retirement + true-up + flag resolution in one unit of work), suggestion rows keyed to
  the flags they decorate, and log entries. Pairing inputs grow to the order snapshot and the
  estimated-cost sale rows, both already persisted by ADR-0045. No new ESI scopes or calls — so
  **no Privacy page change**. Gated behind the entitlement (ADR-0042).
- Wrong confirmations are recoverable: everything booked is a reversing entry away from undone
  (ADR-0045), and the log shows who confirmed what.
- Limits, accepted openly: pairing is per-type and quantity-overlap only, so a move that repackages
  into a *different* type (reprocessing in transit) is out of scope (that's ADR-0047's domain), and
  a genuine loss at A coinciding with same-type activity at B — off-app buyback found in a hangar,
  or off-app stock listed and sold through the buyback division — can masquerade as a move, which
  is exactly why a human confirms (and why sold-portion corrections stay visible and reversible in
  the log rather than silently rewriting sale rows).
- **Move *then* reprocess before a sync is a known blind spot.** The shortfall at A is the source
  type but the excess at B is its materials, so this heuristic can't pair them, and ADR-0047's
  hangar-assisted reprocess suggestion matches shortfall against yield-consistent excess **within
  a hangar** — the composed case needs that yield matching to look cross-location (an ADR-0047
  extension, not attempted here). Until then it degrades to the safe defaults (shortfall flag +
  deemed-cost material lots), with manual reprocess entry as the recovery. (A move followed by a
  **sale** before any sync — once listed here as the same kind of blind spot — is now exactly what
  the sell-side evidence covers.)

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
  without type identity is numerology; scanning raw asset *counts* beyond configured hangars pairs
  against stock the ledger doesn't govern. Rejected; same-type only, hangar counts only where a
  hangar is configured. (The sell-side signals are not an exception to this line: the division's
  orders and fills are ledger-governed facts under ADR-0045, unlike raw asset counts.)
- **Rebook the already-sold rows at real cost on confirm** — an ADR-0045 reversal + re-entry per
  sale row would make per-sale COGS exact, but it reaches into frozen sale facts (and their
  attached transaction tax), quietly changes every report that already included those rows, and
  contradicts the stance the partial-consumption clause just took for hangar pairs. The single
  aggregate true-up yields the same total profit with one visible, reversible correction.
  Rejected; sale rows stay frozen, the estimate is corrected in aggregate.
