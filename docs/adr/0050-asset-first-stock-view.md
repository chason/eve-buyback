# 0050. Asset-first Stock view: the hangar snapshot is the table, the ledger is the cost

- **Status:** Proposed
- **Date:** 2026-08-11
- **Relates to:** [ADR-0044](0044-hangar-inventory-reconciliation.md) (the hourly hangar read
  this persists; its deemed lots and shortfall flags are how the two views converge),
  [ADR-0043](0043-lot-based-buyback-accounting.md) (the lot ledger — still the sole cost engine),
  [ADR-0047](0047-lot-transformations-reprocessing.md) (reprocess children inherit cost and age;
  this ADR promotes the hint to an automatic record),
  [ADR-0049](0049-hangar-move-detection.md) (listed/sold evidence and the escrow subtraction the
  "listed" section reuses), [ADR-0042](0042-paid-accounting-entitlements.md) (the gate)

## Context

The Stock page's table was the **ledger**: open lots rolled up per type, and lots are
materialized from what contracts delivered. A buyback that takes ore therefore showed **ore** —
and kept showing ore after the corp reprocessed it in game, unless someone recorded the
reprocess by hand. What managers actually hold, price, and sell is the **minerals**. The app
already reads the marked hangars' real contents every hour (ADR-0044), but used the read only to
flag discrepancies and then threw it away — the page never showed the one thing the manager
wanted to see: what's actually in the hangar.

Two structural gaps followed. First, the hourly read was transient, so showing it would have
meant an ESI call per page view (and ESI caches corp assets for an hour anyway). Second, hangar
stock that no lot explains (the reprocessed minerals) had no cost or age to display: the
reprocess **hint** (ADR-0047) deliberately never auto-applied, so the ore lots sat open while
the minerals sat unexplained until a human clicked through the suggestion.

## Decision

**1. Persist the snapshot.** Each successful reconciliation pass stores what it counted —
`(location, type) → qty` across the marked hangars, nothing else — in `hangar_stock`, replaced
wholesale per pass, with a one-row `hangar_sync` marker for when. An empty hangar is a valid
snapshot (zero rows, fresh marker); a failed ESI read leaves the previous snapshot and its
honest timestamp in place. Unmarking the last hangar deletes the stored snapshot (the Privacy
page's retention promise). Because the snapshot and the pass's reconciliation artifacts (deemed
lots, flags) are written in one unit of work, page and log always describe the same photograph.

**2. The table shows the snapshot; the ledger prices it.** When a snapshot exists, the Stock
table keys its rows off physical contents — minerals show as minerals. Open lots at the same
`(location, type)` slot join in FIFO, capped at the physical count, supplying "what we paid" and
"sitting for" (a lot's age reaches back to the contract that bought it). Physical units no lot
explains surface as `qty_unbooked` — no cost, unknown age, chipped "not on the books yet" — and
resolve themselves on the next pass (deemed lot or automatic reprocess). Lot units *beyond* the
physical count are deliberately absent from the table: they are the reconciliation's business
(shortfall flag, move card, escrow), not phantom stock to display. The summary cards stay
**ledger-wide** on both bases — what the corp owns on paper doesn't change with how the table is
keyed. Without a snapshot (no marked hangars, or no sync yet) the table falls back to the
pre-ADR ledger view unchanged.

**3. Owned-but-elsewhere stock keeps its own sections.** Sell-order escrow — physically out of
the hangar (ADR-0049) — renders as a compact "Listed for sale" section from the corp order
snapshot (ADR-0045). Declared hauls in transit are already shown by the hauls section; nothing
owned disappears from the page.

**4. The reprocess hint becomes an automatic record.** When a pass sees a reprocessable type
short while its own materials are in excess at the same location, in quantities the missing
units could actually have produced (the ADR-0047 matcher), it **records the reprocess** instead
of suggesting it: source lots consume FIFO, the *observed* material excess becomes the child
lots, and cost and `acquired_at` flow through per source lot — the minerals inherit the ore
contracts' age and cost, exactly as a hand-recorded reprocess would. The link is logged as its
own reconciliation kind, never silent. What ADR-0047 guarded against — guessing yields — doesn't
apply: the outputs recorded are what was counted, not an assumed rate; the only inference is the
link itself, bounded by the ≤100 %-yield consistency check. When the pattern can't be applied
cleanly (insufficient idle source lots), the pass falls back to the suggest-only hint.

## Consequences

- Managers see reality: minerals as minerals, with contract-derived age and cost wherever the
  ledger can supply them, "as of" the last hangar check.
- Hangar contents are now **stored** (type + quantity only). The Privacy page documents the
  retention and the unmark-deletes-it rule in the same change.
- `unrealized` compares the market's answer against the **booked portion** only — an unbooked
  stack has no basis to be up or down against.
- The automatic reprocess is a behavior change to ADR-0047's "suggest, never auto-apply" stance,
  deliberately accepted: cost conservation still holds (children carry exactly the consumed
  cost), the transformation audit row still writes, and the log still tells the human what
  happened. A wrong link is correctable the way every entry is — reversing entries, never edits.
- Ledger rows with no location, and stock at unmarked locations, appear in the totals but not in
  the hangar table; the reconciliation log and move cards remain the place they surface.
