# 0002. SQLite via SQLAlchemy 2.0 + Alembic, Postgres-ready

- **Status:** Accepted
- **Date:** 2026-06-04

## Context

A core goal is that a small corporation can self-host **without standing up extra
software**. Write volume is low (config edits, occasional quotes). But we also
want a credible path to a hosted, multi-corp deployment later, where concurrency
and richer types matter.

## Decision

Use **SQLAlchemy 2.0** (async) as the data-access layer with **Alembic** for
migrations, running on **SQLite** for the MVP. Keep all queries portable (no
SQLite-only SQL) so swapping the connection URL to **PostgreSQL** is a config
change plus a migration run.

## Consequences

- Zero-dependency self-hosting: the database is a file on disk.
- The ORM abstraction and Alembic migrations make the Postgres move low-risk; CI
  can run the suite against both to keep it honest.
- SQLite's single-writer model is acceptable for MVP scale; enable WAL mode.
- Care needed: avoid SQLite-specific behaviors (e.g. lax typing); test constraints
  the way Postgres would enforce them.
- JSON columns (e.g. saved appraisal line items) work on both via SQLAlchemy's
  `JSON` type.

## Alternatives considered

- **PostgreSQL from day one** — best concurrency/types, but adds an operational
  dependency that conflicts with the "no other software" goal for the MVP.
- **SQLite with no ORM/abstraction** — simplest now, but a painful rewrite to move
  to Postgres later.
- **SQLModel** — convenient ORM+Pydantic blend, but couples the two layers and
  lags SQLAlchemy 2.0 features; we prefer explicit separation.
