# 0024. PostgreSQL as the sole database

- **Status:** Accepted
- **Date:** 2026-06-07
- **Supersedes:** [0002](0002-sqlite-sqlalchemy-postgres-ready.md)
- **Relates to:** [0012](0012-single-deployable-packaging.md) (deployment), [0020](0020-decimal-money-values.md) (Decimal)

## Context

[ADR-0002](0002-sqlite-sqlalchemy-postgres-ready.md) ran on SQLite with a
"Postgres-ready" posture. That split kept producing **dev/prod divergences**:
`Numeric` stores as REAL (lossy) on SQLite (ADR-0020 caveat), `DateTime(timezone=True)`
returns naive datetimes, the upserts were dialect-pinned, and — the trigger —
`Mapped[int]` is 64-bit on SQLite but **32-bit `INTEGER` on Postgres**, so a real EVE
`quantity` over 2.1 billion would store in dev and fail in prod. Each was a latent bug
waiting for the eventual Postgres deployment.

## Decision

Use **PostgreSQL as the sole database** — dev, test, and prod — via the **asyncpg**
async driver. Drop SQLite entirely (`aiosqlite` removed; the SQLite PRAGMA block and
Alembic `render_as_batch` gone; the four ON-CONFLICT upserts use the `postgresql`
dialect). Since this is greenfield (no shipped data), the SQLite-authored migration
chain is **squashed to a single Postgres-native baseline**.

Consequences of going all-Postgres, now fixed for real:
- **`Numeric` is exact** (real `NUMERIC`) — the ADR-0020 SQLite-REAL caveat is moot.
- **`DateTime(timezone=True)` is tz-aware** end-to-end — the `_as_utc` naive guard is
  deleted.
- **`quantity` is `BigInteger`** (and DTO-bounded), so large EVE hauls work everywhere.
- Tests run against a dedicated `<name>_test` database (same isolation the SQLite
  `test_*.db` file gave); the engine pool is disposed per test so asyncpg connections
  don't outlive their event loop.

## Consequences

- **One database engine, one set of behaviors** — no dialect branching, no
  divergence-by-environment. Dev mirrors prod.
- **Ops cost:** dev, test, CI, and self-host now need a Postgres server, partly walking
  back [ADR-0012](0012-single-deployable-packaging.md)'s "one SQLite file, minimal moving
  parts." Accepted for correctness; a single Postgres container is still simple to
  self-host, and packaging that is a later concern.
- Local setup gains a one-time step: create the `buyback` + `buyback_test` databases and
  set `BUYBACK_DATABASE_URL` in `.env`.

## Alternatives considered

- **Keep SQLite, patch the schema to be Postgres-safe** (e.g. `BigInteger`) — would let
  the zero-infra test loop survive, but leaves the REAL/datetime/dialect divergences and
  the "verified on SQLite, runs on Postgres" gap. Rejected: the point is to delete the
  divergence class, not narrow it.
- **Dual SQLite/Postgres support** (dialect-dispatch upserts, both drivers) — preserves
  fast local tests but keeps the very divergence we're eliminating. Rejected.
