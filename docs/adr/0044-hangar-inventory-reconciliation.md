# 0044. Hangar inventory reconciliation (ESI corp assets vs derived idle stock)

- **Status:** Proposed
- **Date:** 2026-07-09
- **Relates to:** [ADR-0043](0043-lot-based-buyback-accounting.md) (the lot ledger this
  reconciles + `qty_idle` derivation), [ADR-0029](0029-encrypted-refresh-token-structures.md) /
  [ADR-0036](0036-corp-roster-manager-designation.md) (the Corp ESI token this adds a scope to),
  [ADR-0037](0037-corp-contract-watcher.md) (the 403-skip / reconnect pattern reused),
  [ADR-0034](0034-background-market-refresh.md) (the scheduler), [ADR-0006](0006-market-data-fuzzwork.md)
  (deemed-cost pricing), [ADR-0045](0045-esi-sales-ingestion-and-manual-entry.md) (shared
  reconciliation log), [ADR-0042](0042-paid-accounting-entitlements.md) (the gate)

## Context

The lot ledger (ADR-0043) is a **perpetual book** and will drift from physical reality: buyback
happens off-app, sales go unrecorded, items get moved or repackaged. Managers also need to seed
**existing** stock (opening balances) without hand-keying it. EVE exposes corp assets over ESI,
so we can reconcile the book against the **actual hangar**.

## Decision

**Periodically read the buyback hangar via ESI and reconcile it against derived idle stock:
auto-create deemed-cost lots for unexplained excess, flag shortfalls, and log every change.**

- **Read corp assets.** `GET /corporations/{id}/assets/` — new scope
  `esi-assets.read_corporation_assets.v1` (Director role). `location_flag` (`CorpSAG1`…`CorpSAG7`)
  + `location_id` identify the hangar. Config names which location(s) + division(s) are "the
  buyback hangar."
- **The ledger stays the source of truth; the hangar is a physical count.** Assets are only
  `type → qty` with no cost basis, acquisition date, or FIFO order, so they **reconcile against**
  — never replace — the lot ledger (like a stock-take vs perpetual inventory).
- **Reconcile per `(location, type)` against `qty_idle`, not `qty_remaining`.** Listed,
  contracted, and in-transit units have physically **left** the hangar (market/contract escrow, a
  freighter), so `expected_in_hangar = Σ qty_idle`. This is exactly why lot state is derived
  (ADR-0043).
- **Excess (hangar > expected) → off-app buyback.** Auto-create a **deemed-cost lot**
  (`cost_is_estimated = TRUE`) for the delta, valued by the corp's own buyback rules × current
  price, falling back to 90% Jita buy. Deemed cost is **fixed at creation and never re-priced**;
  creation is **idempotent on the `(location, type)` delta**, so repeated syncs don't thrash lots
  as prices wiggle.
- **Shortfall (hangar < expected) → flag for a human.** Can't be auto-resolved by inventing a lot
  (it means an unrecorded sale/move); surfaced as a reconciliation item.
- **No approval queue — a flag plus a log.** Estimated lots auto-create (flagged and badged
  "Estimated value"); a **reconciliation log** records what each sync changed ("+40 Tritanium
  added as estimated; −12 Veldspar short"). Rationale: `cost_is_estimated` already keeps
  estimated/measured separable everywhere (and it propagates through FIFO into realized profit),
  so a blocking queue would only create an **ignored backlog** and a knowingly-wrong balance
  sheet. An optional anomaly threshold escalates unusually large excess for a look.
- **Doubles as the opening-balance importer.** The first run seeds all existing stock as
  deemed-cost lots — replacing a manual opening-balance tool.

## Consequences

- Off-app buyback is captured for free, and opening balances are imported from ESI rather than
  hand-keyed. Manual lot entry (ADR-0045) becomes the **override**, not the main path.
- New scope → **Privacy page update** + a one-time **reconnect** (tokens granted before this lack
  it). Until reconnected — or if the character lacks the Director role — the sync **degrades
  gracefully**, logging and skipping without flagging the token failed (mirrors ADR-0037's
  403-skip). Rides the ADR-0034 scheduler.
- **Valuation discipline:** matched stock stays at its **recorded cost**; only *unexplained
  excess* gets deemed cost — matched items are **never** re-priced at the current rule (the cost
  principle, ADR-0043).
- The reconciliation log is a **shared surface** with the sale exceptions in ADR-0045 — one
  "Needs a look" list.

## Alternatives considered

- **Hangar read as the primary inventory source** — it has no cost basis or FIFO order, so it's a
  count, not a ledger; it can only reconcile. Rejected.
- **A blocking approval queue for every deemed-cost lot** — creates an ignored backlog and leaves
  the balance sheet knowingly understated; the `cost_is_estimated` flag + log already prevent
  silent blending, and lots are reversible. Rejected in favor of flag-plus-log, reserving human
  review for shortfalls and outlier spikes.
- **Manual opening-balance entry as the primary path** — pure toil; the hangar sync absorbs it.
  Rejected (manual stays as the override).
- **Reconcile against `qty_remaining`** — would count listed/escrowed/in-transit stock as
  "missing" every sync. Rejected; compare against `qty_idle`.
