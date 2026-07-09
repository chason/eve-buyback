# 0043. Lot-based buyback accounting (FIFO lots, cost basis, NRV)

- **Status:** Proposed
- **Date:** 2026-07-09
- **Relates to:** [ADR-0037](0037-corp-contract-watcher.md) (the completed contract that is the
  acquisition event), [ADR-0021](0021-appraisal-computation-and-storage.md) /
  [ADR-0014](0014-persisted-appraisals.md) (the appraisal + `AppraisalLine.unit_price` cost
  source), [ADR-0020](0020-decimal-money-values.md) (Decimal money),
  [ADR-0025](0025-uuid-primary-keys.md) (UUID PKs, EVE ids as columns),
  [ADR-0006](0006-market-data-fuzzwork.md) / [ADR-0028](0028-esi-market-source-and-aggregation.md)
  (the market cache reused for NRV), [ADR-0030](0030-buyback-drop-off-locations.md) (member hauls
  in — why hauling is a selling cost), [ADR-0042](0042-paid-accounting-entitlements.md) (the gate),
  [ADR-0044](0044-hangar-inventory-reconciliation.md) / [ADR-0045](0045-esi-sales-ingestion-and-manual-entry.md)
  (inventory truth + sale events built on this ledger)

## Context

Buyback managers need two separate answers: **how much realized profit** did the buyback make
over a period, and **what does it own right now** — at cost, not at hoped-for sale price. That
requires real inventory accounting. Fortunately the app already persists the acquisition event:
a **completed appraisal contract** (ADR-0037) is a verified purchase — exact items, price, and
location, already validated against EVE. Per-line cost sits in `AppraisalLine.unit_price`
(ADR-0021), and money is Decimal (ADR-0020). This ADR defines the ledger those facts feed.

## Decision

**A lot ledger: every acquisition creates lots; lots are consumed FIFO on sale; inventory is
carried at landed cost; realized and unrealized profit are always reported separately.**

- **The lot.** `lots(id UUID, item_type_id, qty_original, qty_remaining, unit_purchase_cost
  Numeric, unit_hauling_cost Numeric=0, acquired_at, source 'buyback'|'opening_balance'|'manual',
  appraisal_id FK?, cost_is_estimated bool, location_id, written_down_to Numeric?, notes)`.
  **Landed unit cost = `unit_purchase_cost + unit_hauling_cost`, floored to `written_down_to`** —
  derived, never stored.
- **Lots are born from completed appraisal contracts.** When the contract watcher (ADR-0037)
  flips an appraisal to `completed`, materialize **one lot per accepted line**, with
  `unit_purchase_cost = AppraisalLine.unit_price` (the ISK the corp actually pays per unit, net
  of the buyback %) and `cost_is_estimated = FALSE`. **Idempotent**, keyed on `appraisal_id`
  (once-only); `completed` is terminal for lot creation (EVE finished contracts don't un-finish).
- **Money is Decimal** (ADR-0020) — sums run to trillions of ISK; float would drift.
- **FIFO consumption.** Oldest lot of that `(type, location)` first. A sale writes **one `sales`
  row per lot touched** (COGS is per-lot). FIFO is a pure `domain/` function.
- **Hauling is a *selling* cost, not part of landed cost.** In this app the member hauls items
  to the drop-off (ADR-0030), so **inbound** hauling is not the corp's cost — inbound lots start
  at pure purchase cost (`unit_hauling_cost = 0`). The corp's freight to move stock to a sell hub
  is a **selling expense** attributed to the lots shipped (ADR-0045).
- **NRV comes from the existing market cache.** Net realizable value uses `MarketPrice`
  (ADR-0006/0028) aggregates — **no separate price-snapshot table**. Write-down: if `NRV < landed
  cost`, set `written_down_to = NRV` and book the loss in that period; **never write back up**
  (conservatism). NRV is *best-available cached* market value (TTL cache, only-warm hubs), not a
  guaranteed rolling average — acceptable; a true time-average is a separate Fuzzwork-field
  question.
- **Lot state is derived, not stored.** `qty_idle = qty_remaining − on_orders − on_contracts −
  in_transit`. A lot can split across states, so state is computed from allocation link tables
  (orders/contracts/shipments), never a column. (ADR-0044 relies on this.)
- **Opening balances / off-app buyback** enter as lots with `source
  opening_balance`/`manual` and `cost_is_estimated = TRUE` at **deemed cost** (buyback rule ×
  current price, fallback 90% Jita buy). Reports segment by `cost_is_estimated` so measured and
  estimated **never silently blend**; the flag propagates through FIFO into each sale's realized
  profit.
- **Reports** (the queries that justify the model): balance sheet (cash from the wallet,
  inventory at cost split by state, receivables, unrealized gain/loss as its **own line**);
  income statement (revenue − COGS − selling expenses, segmented by channel + `cost_is_estimated`);
  inventory aging/turnover; per-type margin.

## Plain-English UI (cross-cutting constraint)

Users are EVE players, not accountants. The **rigor stays in the data model**; the **UI is a
plain-English skin** — "Profit", "What the buyback has now", "What we paid for it", "worth less
than we paid" — and **never** surfaces the words lot, FIFO, COGS, NRV, or reconciliation. This
constraint binds ADR-0043/0044/0045 alike.

## Consequences

- Buyback-sourced lots have a **verified, exact** cost basis and are rebuildable from appraisals;
  only opening-balance/off-app lots are estimates, always flagged.
- New tables + a `domain/` module (FIFO, NRV, realized-profit pure functions) + application use
  cases + `records.py` read-models, all gated behind the entitlement (ADR-0042).
- The **position half** (inventory at cost, aging, unrealized G/L) needs **no new ESI** — it
  derives from appraisals + the market cache — so it ships with **no Privacy change**. The
  **performance/cash half** needs new scopes (ADR-0045).

## Alternatives considered

- **Weighted-average cost** — loses per-purchase traceability and makes aging fuzzy; FIFO keeps a
  clean audit trail. Rejected.
- **Carry inventory at current/sale price** — violates the cost principle and books unrealized
  gains as if realized. Rejected; carry at cost, report unrealized separately.
- **A dedicated `price_snapshots` table** — duplicates `MarketPrice`; reuse the cache and record
  the struck NRV on the lot (`written_down_to`) as the only history needed. Rejected.
- **Store lot state as a status column** — a lot spans states (part listed, part idle); derive
  from allocations instead. Rejected.
- **Float money** — precision loss on trillion-ISK sums; Decimal per ADR-0020. Rejected.
