# 0020. Decimal for money and quantity values, not float

- **Status:** Accepted
- **Date:** 2026-06-07
- **Relates to:** [0006](0006-market-data-fuzzwork.md) (price cache), [0009](0009-sde-reference-data.md) (SDE volume), [0014](0014-persisted-appraisals.md) (immutable appraisals), [0002](0002-sqlite-sqlalchemy-postgres-ready.md) (Postgres-ready)

## Context

The buyback's whole job is to compute ISK a member gets paid. That number is
derived from market aggregates (`unit * percentage / 100 * quantity`, summed over
many lines — see [ADR-0014](0014-persisted-appraisals.md)) and **persisted as an
immutable snapshot**: it must be exact and reproducible, the same every time the
appraisal is read back.

`float` (IEEE-754 double) is the wrong representation for this. It can't represent
most decimal fractions exactly (`0.1 + 0.2 != 0.3`), and EVE values span ~5 ISK
(Tritanium) to trillions for a large appraisal — near float64's ~15–16 significant
digits, so summing many line totals silently loses sub-ISK precision exactly when
totals get large. The market aggregates also arrive from Fuzzwork as JSON text;
parsing them through `float` discards digits that parsing straight to `Decimal`
would keep.

The usual objection to `Decimal` is speed, but this workload is a handful of
multiplications per appraisal, not a hot loop — the difference is unmeasurable. The
real cost is storage fidelity on SQLite (below), which is acceptable.

## Decision

Use **`Decimal` (SQLAlchemy `Numeric`, Pydantic `Decimal`) everywhere we previously
used `float`** — both money (market price aggregates) and quantities
(`SdeType.volume`, market order volumes). Order counts stay `int`.

- **Columns** are unconstrained `Numeric()` — arbitrary precision on PostgreSQL
  ([ADR-0002](0002-sqlite-sqlalchemy-postgres-ready.md)), so no fixed scale caps the
  decimals we keep.
- **Ingest parses to `Decimal` at the boundary**: `FuzzworkSide` fields and
  `SdeTypeRow.volume` are `Decimal`, built from the wire/CSV **text** so the exact
  source digits are preserved (no `float` round-trip).
- **Rounding is a domain decision applied at computation time, not storage.** The
  payout rounding policy (e.g. round-half-up to whole ISK) lives in the pricing/
  appraisal domain logic landing in M5; the cache and reference tables store the
  full-precision values.

## Consequences

- Money math is exact and appraisals are reproducible — the property
  [ADR-0014](0014-persisted-appraisals.md) needs.
- **SQLite caveat (now moot — [ADR-0024](0024-postgresql-database.md)):** under SQLite,
  `NUMERIC` had REAL (float) affinity, so values round-tripped through a float in local
  dev. Since the project moved to PostgreSQL as the sole database, `NUMERIC` is exact
  everywhere and this caveat no longer applies.
- Pydantic serializes `Decimal` to a JSON number by default; API DTOs that expose
  money (M5 appraisals) inherit this — confirm the SPA renders it acceptably then.
- Cached timestamps from SQLite come back naive; freshness math normalizes them to
  UTC in the market use case (unrelated to Decimal, noted for completeness).

## Alternatives considered

- **Keep `float`** — simplest, but accepts non-reproducible cent-level payout totals
  and float drift in sums; unacceptable for a number someone is paid and that is
  frozen forever.
- **`Decimal` only for the appraisal, `float` for the cache** — the cache feeds the
  computation, so `Decimal(float_price)` would inherit the float's error; a clean
  pipeline requires `Decimal` from ingest.
- **Scaled integers (ISK×100 as BIGINT)** — exact and fast on any DB, but forces a
  rounding/scale decision on every aggregate at ingest and complicates arithmetic;
  rejected as premature for reference/cache data whose precision we'd rather keep.
- **Fixed-scale `Numeric(p, s)`** — predictable DDL, but caps retained decimals;
  unconstrained `Numeric` keeps maximal precision and defers rounding to the domain.
