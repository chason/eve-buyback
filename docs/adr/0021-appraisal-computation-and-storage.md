# 0021. Appraisal computation, storage, and closed-set typing

- **Status:** Accepted
- **Date:** 2026-06-07
- **Relates to:** [0007](0007-pricing-rule-taxonomy.md) (resolution), [0008](0008-data-quality-rejection.md) (rejection), [0014](0014-persisted-appraisals.md) (persistence), [0020](0020-decimal-money-values.md) (Decimal money)

## Context

Milestone 5 turns the configured pricing rules + market cache into the product's
core action: a member submits a list of items and gets a **persisted, immutable
appraisal** ([ADR-0014](0014-persisted-appraisals.md)). Several details ADR-0014 and
ADR-0008 left open had to be pinned down to implement it, and the new tables
introduced the project's first *stored* closed-set fields (basis, status, …), which
needed a typing convention.

## Decision

1. **Structured item input.** `POST /appraisals` accepts `{items:[{type_id,
   quantity}]}` only. The M6 SPA composes this from the type-search picker; raw EVE
   inventory-paste parsing is deferred to land with that UI.

2. **Minimal data-quality rejection for now.** A line is rejected only when there is
   **no usable price**: the type isn't in the seeded SDE ("Unknown item"), no cached
   market data exists for it ("No market data"), or the resolved basis's side has no
   orders ("No buy/sell orders"). The *configurable* liquidity/staleness/spread
   thresholds of [ADR-0008](0008-data-quality-rejection.md) remain M7 — no threshold
   columns are added to `BuybackConfig` yet. Rejected lines store `line_total = 0`, a
   reason, and null pricing fields, and are excluded from `accepted_total`.

3. **Hybrid line storage.** The authoritative per-line snapshot lives in relational
   `appraisal_lines` rows (queryable, typed). In addition, the parent `appraisals`
   row keeps a **log-only `request_json`** column holding the exact request payload,
   documented (model docstring) as an audit/debugging copy that must **not** be
   queried under normal operation.

4. **Banker's rounding to 2 dp.** `unit_price` and `line_total` are rounded with
   `Decimal.quantize(0.01, ROUND_HALF_EVEN)`; `accepted_total` sums the rounded line
   totals. Half-even is unbiased over many summed lines and is Python's `Decimal`
   default. This realizes the rounding policy [ADR-0020](0020-decimal-money-values.md)
   deferred. (On SQLite, money round-trips through REAL affinity per ADR-0020, so
   stored values may display extra trailing precision in dev; exact on PostgreSQL.)

5. **Closed-set fields = domain `Literal` + portable `CHECK`.** Each closed set
   (`Basis`, `AggregateField`, `TargetKind`, `LineStatus`) is a `Literal` in
   `domain/pricing.py` — the single source of truth. The same `Literal` types the
   domain functions, validates the API DTOs (Pydantic → 422), and builds the DB
   column via `sqlalchemy.Enum(*get_args(L), native_enum=False, create_constraint=True)`
   → a `VARCHAR + CHECK (col IN (...))` on both SQLite and PostgreSQL, with no
   native-ENUM `ALTER TYPE` migration friction. A bad value is caught at all three
   layers. This follows the existing `Role = Literal` convention but adds DB
   enforcement because these values are stored and money-critical.

Resolution itself is the most-specific-wins walk from
[ADR-0007](0007-pricing-rule-taxonomy.md), implemented as a pure function in
`domain/pricing.py`. Every registered corp gets a default "90% Jita Buy" config row at
registration so config/rules/appraisals always have a baseline.

## Consequences

- Appraisals are reproducible audit records: each line stores the resolved
  basis/percentage and the market unit value used, so it explains itself without
  re-reading rules or prices.
- The hybrid storage keeps lines queryable while preserving the raw request for
  debugging; the "do not query `request_json`" rule must be respected.
- Deferring configurable data-quality keeps M5 focused; the rejection plumbing
  (per-line status + reason) is already in place for M7 to extend.
- The `Literal`+CHECK convention is now the standard for stored enums; new closed-set
  columns should use `check_enum(...)`.

## Alternatives considered

- **JSON-only lines** — simplest, but not queryable and weakly typed; rejected in
  favor of the hybrid (rows authoritative, JSON for audit).
- **Half-up rounding** — familiar, but biases sums upward; half-even is fairer for a
  payout total aggregated over many lines.
- **Python `Enum` classes / plain `str`** — Enums add an unused-in-this-codebase
  pattern and native-ENUM migration friction; plain `str` gives no DB guard. The
  `Literal`+CHECK middle ground matches the existing convention and still enforces.
