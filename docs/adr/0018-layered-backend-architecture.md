# 0018. Layered backend architecture

- **Status:** Accepted
- **Date:** 2026-06-07
- **Relates to:** [0001](0001-fastapi-backend.md) (FastAPI), [0002](0002-sqlite-sqlalchemy-postgres-ready.md) (SQLAlchemy), [0004](0004-eve-sso-session-auth.md) (SSO), [0016](0016-per-request-role-resolution.md) (role resolution)

## Context

Through Milestones 1–3 the backend grew organically: FastAPI routers held HTTP
concerns, business rules, ORM queries, and external-API calls all in one place
(`api/v1/auth.py`, `api/v1/corporations.py`). That was fine at three endpoints, but
the upcoming pricing/appraisal work (M4–M5) adds market gateways, a rule-resolution
engine, and persisted appraisals. Continuing to pile logic into routers would make
the code hard to test in isolation, hard to reason about, and hard for an agent to
navigate by responsibility.

We want explicit seams: a clear home for each kind of logic, and a dependency
direction that prevents transport/HTTP details from leaking into business logic or
the database.

## Decision

Organize the backend into five layers under `backend/app/`, with **dependencies
pointing inward/downward only** — an outer layer may import the one beneath it, never
the reverse:

```
interface  →  application  →  domain
                  ↓
                data        plugins
```

- **`interface/`** — the API. FastAPI routers (`v1/`) plus session/auth dependencies,
  the CSRF middleware, the DB-session provider, and the `ApplicationError → HTTP
  status` mapping. Routers hold **only** API concerns and call the application layer.
- **`application/`** — **use cases**, one function per user action. Orchestrates
  `plugins/`, `domain/`, and `data/`; **owns the unit of work** (`session.commit()`);
  raises typed errors (`errors.py`). No HTTP, no SQL.
- **`domain/`** — small, single-purpose **pure functions**, no I/O.
- **`data/`** — all database logic. `models/` (ORM) and `repositories/` (queries) in
  **separate files**. Repositories **return Pydantic read-models** (`records.py`),
  never ORM entities, and `flush` rather than `commit` (the application owns the
  transaction).
- **`plugins/`** — gateways to **outside APIs** (EVE ESI, EVE SSO). Named for "all
  code that talks to an external API lives here." Like the data layer, they **return
  Pydantic** whenever they hand back data.
- **`schemas/`** — Pydantic **API DTOs**. The interface maps `data` records and
  `application` results to these, so the internal model shape and the public API
  contract stay decoupled.

## Consequences

- **Testability:** use cases can be exercised without an HTTP client; plugins/repos
  are trivially fakeable. The existing suite passed **unchanged** through the
  refactor, and `alembic check` reported no drift — evidence the move was
  behavior-preserving.
- **Navigability:** code is found by responsibility, and layer-local conventions are
  documented where they apply ([ADR-0019](0019-progressive-layer-documentation.md)).
- **Cost:** some mapping boilerplate (`record → DTO`) and more, smaller files. Judged
  worth it for the seams.
- New features follow the pattern: a use case in `application/`, pure logic in
  `domain/`, persistence in a `data/` repository returning a record, a thin
  `interface/v1/` router, and outside-API access via a `plugins/` gateway.

## Alternatives considered

- **Keep flat routers** — simplest, but mixes four concerns per handler and does not
  scale to the pricing/appraisal surface; rejected.
- **Fold external clients into the data layer** — fewer top-level packages, but the
  data layer is defined as database logic; mixing remote-API I/O there blurs that.
  Rejected in favor of a dedicated `plugins/` layer.
- **Reuse `schemas/` DTOs as the data layer's return type** — less duplication, but
  couples the database's output shape to the public API contract. Rejected in favor of
  dedicated `data/records.py` read-models.
