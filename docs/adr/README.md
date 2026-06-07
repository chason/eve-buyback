# Architecture Decision Records

This directory records the significant architectural decisions for the buyback
project. Each ADR captures the **context**, the **decision**, its
**consequences**, and the **alternatives** that were weighed — so a future reader
(human or agent) understands *why*, not just *what*.

Format: lightweight [MADR](https://adr.github.io/madr/)-style. New ADRs supersede
old ones rather than being edited away; mark the old one `Superseded by NNNN`.

| # | Title | Status |
|---|-------|--------|
| [0001](0001-fastapi-backend.md) | REST backend on FastAPI + Pydantic | Accepted |
| [0002](0002-sqlite-sqlalchemy-postgres-ready.md) | SQLite via SQLAlchemy 2.0 + Alembic, Postgres-ready | Accepted |
| [0003](0003-multi-tenant-corp-scoping.md) | Multi-tenancy via corp_id row scoping | Accepted |
| [0004](0004-eve-sso-session-auth.md) | EVE SSO login with backend-issued session cookie | Accepted |
| [0005](0005-authorization-roles.md) | Authorization roles: member / manager / CEO | Accepted |
| [0006](0006-market-data-fuzzwork.md) | Market data from Fuzzwork aggregates, cached | Accepted |
| [0007](0007-pricing-rule-taxonomy.md) | Pricing rules on EVE market groups + overrides | Accepted |
| [0008](0008-data-quality-rejection.md) | Configurable data-quality rejection | Accepted |
| [0009](0009-sde-reference-data.md) | Seed a subset of the EVE SDE | Accepted |
| [0010](0010-in-process-scheduling.md) | In-process scheduling, no external broker | Accepted |
| [0011](0011-api-contract-and-typescript-types.md) | Versioned API + TS types from OpenAPI | Accepted |
| [0012](0012-single-deployable-packaging.md) | Single deployable; backend serves the SPA | Accepted |
| [0013](0013-frontend-stack.md) | Frontend: React + Vite + TanStack Query | Accepted |
| [0014](0014-persisted-appraisals.md) | Persisted, immutable appraisals with shareable ids | Accepted |
| [0015](0015-corp-registration-ceo-or-director.md) | Corp registration by CEO or Director | Accepted |
| [0016](0016-per-request-role-resolution.md) | Resolve the app role from the DB per request | Accepted |
| [0017](0017-csrf-custom-header.md) | CSRF: SameSite=lax plus a required custom header | Accepted |
| [0018](0018-layered-backend-architecture.md) | Layered backend: interface / application / domain / data / plugins | Accepted |
| [0019](0019-progressive-layer-documentation.md) | Progressive docs via layer-local CLAUDE.md | Accepted |
| [0020](0020-decimal-money-values.md) | Decimal (not float) for money and quantity values | Accepted |

See the project [architecture overview](../architecture.md) for how these fit together.
