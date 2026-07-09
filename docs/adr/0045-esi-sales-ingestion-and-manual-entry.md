# 0045. ESI outgoing-sales ingestion + manual-entry escape hatch

- **Status:** Proposed
- **Date:** 2026-07-09
- **Relates to:** [ADR-0043](0043-lot-based-buyback-accounting.md) (the lot ledger sales consume),
  [ADR-0037](0037-corp-contract-watcher.md) (the contract watcher reused for outgoing contracts),
  [ADR-0044](0044-hangar-inventory-reconciliation.md) (the shared reconciliation log + inventory
  coupling), [ADR-0029](0029-encrypted-refresh-token-structures.md) /
  [ADR-0036](0036-corp-roster-manager-designation.md) (the Corp ESI token these scopes join),
  [ADR-0020](0020-decimal-money-values.md) (Decimal money), [ADR-0034](0034-background-market-refresh.md)
  (the scheduler), [ADR-0042](0042-paid-accounting-entitlements.md) (the gate)

## Context

The lot ledger (ADR-0043) needs **sale events** to recognize revenue and consume lots FIFO.
Sales leave the buyback mostly via the **market** and via **contracts**. We want to record them
automatically wherever ESI can see them, plus a **manual escape hatch** for what it can't (off-
game deals) and for mistakes (ISK paid into the wrong wallet). An empirical question — do corp
market proceeds land in a specific wallet division, or are they forced to the master wallet? —
was **settled by a probe**: proceeds, tax, fees, and fills all follow the wallet division the
corp market order is **bound to**, which the buyback controls (division 3 in the tested corp).

## Decision

**Ingest outgoing sales from ESI across channels, record each as a FIFO-consuming sale, and
provide a manual-entry escape hatch — with a single "buyback wallet division" driving both the
cash line and sell-side ingestion.**

- **Market channel — three reads, three jobs.** *Wallet transactions*
  (`/corporations/{id}/wallets/{division}/transactions/`) are the **fills** (type, qty,
  unit_price, date, location, unique `transaction_id` → idempotency key) → `sales` + FIFO.
  *Wallet journal* (same division) supplies `transaction_tax` + `brokers_fee` → `expenses`. *Corp
  market orders* (`/orders/` + `/orders/history/`) give the **listed state** (which lots are on
  the market → `qty_idle`, NRV list price). Scopes: `esi-wallet.read_corporation_wallets.v1`
  (Accountant), `esi-markets.read_corporation_orders.v1`.
- **One buyback wallet division (probe-confirmed).** Corp market proceeds/tax/fees/fills all land
  in the division the corp order is bound to — **not** master-forced — so a single config value
  drives the balance-sheet **cash line** (division balance) *and* sell-side journal/transaction
  ingestion. **Guard:** market activity appearing in a division **other** than the configured one
  is surfaced in the reconciliation log ("market activity in an unexpected division") rather than
  dropped — `wallet_division` is chosen per order by the trader and could be fat-fingered.
- **Contract channel — two cases.** *In-game outgoing contracts* (corp → buyer) **reuse the
  contract watcher** (ADR-0037) filtered to issued contracts: `finished` = sale, `price` =
  proceeds, items = disposed, `contract_id` = idempotency key (contracts scope already granted).
  *Off-game negotiated deals* aren't in ESI → **manual entry**, with the "paid" confirmation
  matched against a wallet-journal ISK-transfer entry.
- **Reconciliation reduction.** Each detected sale (market transaction or finished outgoing
  contract): identify `type + qty + proceeds + time + location + channel` → **FIFO-consume** the
  oldest lots → one `sales` row per lot → attach journal tax/fees as `expenses` → realized profit
  = `proceeds − Σ(landed cost × qty) − fees`, segmented by channel + `cost_is_estimated`.
- **No-lot sale coupling.** If ESI reports a sale of a type with **no open lot** (off-app stock
  not yet reconciled), revenue is real but COGS is undefined → fall back to a **deemed COGS**
  (flagged estimated) *or* raise a reconciliation exception. Inventory reconciliation (ADR-0044)
  must stay **ahead of** sales.
- **Manual-entry escape hatch.** A manager can record a manual **sale** (off-game deals), **lot**
  (known-cost off-app buyback), **expense**, or **cash correction** — into the **same**
  `sales`/`lots`/`expenses` tables, tagged `source='manual'`, `entered_by`, `note`, and badged.
  **Two independent flags, never conflated:** `source` (esi | manual) = *provenance*;
  `cost_is_estimated` (measured | deemed) = *cost confidence*.
- **Wrong-wallet is a cash-location problem, not revenue.** ISK landing in a personal or wrong
  wallet is modeled as an **internal receivable** (a real asset, honest balance sheet); the
  eventual ESI-visible transfer **clears** it — no double count. Only **sale events** create
  revenue; a raw ISK deposit is pure cash movement.
- **Corrections are reversing entries, never edits/deletes.** Fixing a mistake = a new offsetting
  entry pointing at what it reverses. For a paid feature the audit trail is the trust.
- **Unified with the reconciliation log.** Hangar shortfalls (ADR-0044) and no-lot sales are the
  exceptions a manager **resolves with a manual entry** — one plain-English "Needs a look" list.

## Consequences

- Market and in-game-contract disposal are **fully automated**; off-game deals and corrections
  are manual. Idempotency via `transaction_id` / `contract_id` means re-polling never double-
  records.
- New scopes (wallet + market orders; the contracts scope already exists) → **Privacy page
  update** + one-time **reconnect** + graceful degrade on a missing scope/role (ADR-0037 pattern).
  Reuses the ADR-0034 scheduler and the ADR-0037 watcher.
- Gated behind the entitlement (ADR-0042). Money is Decimal (ADR-0020).

## Alternatives considered

- **Derive sales from market-order `volume_remain` deltas** — misses exact fills, tax, and buyers;
  the transactions endpoint is authoritative. Rejected.
- **Assume corp market proceeds hit the master wallet (division 1)** — the probe disproved it;
  proceeds follow the order's `wallet_division`. Rejected.
- **Hard-delete/edit booked entries to fix mistakes** — destroys the audit trail; reversing
  entries preserve it. Rejected.
- **A separate manual side-ledger** — fragments reporting; the same tables + a `source` flag keep
  one set of books. Rejected.
- **Treat wrong-wallet ISK as a revenue adjustment** — double-counts when the ISK is later moved
  to the right wallet (ESI then sees the transfer). Model it as a receivable instead. Rejected.
