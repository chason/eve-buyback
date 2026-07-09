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
| [0002](0002-sqlite-sqlalchemy-postgres-ready.md) | SQLite via SQLAlchemy 2.0 + Alembic, Postgres-ready | Superseded by 0024 |
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
| [0021](0021-appraisal-computation-and-storage.md) | Appraisal computation, storage, and closed-set typing | Accepted |
| [0022](0022-no-sequential-pks-in-api.md) | Don't expose sequential surrogate PKs in the API | Accepted |
| [0023](0023-frontend-styling-and-typegen.md) | Frontend styling (Pico.css) and OpenAPI type generation | Accepted |
| [0024](0024-postgresql-database.md) | PostgreSQL as the sole database | Accepted |
| [0025](0025-uuid-primary-keys.md) | UUID primary keys for app entities; EVE ids as unique columns | Accepted |
| [0026](0026-ore-reprocess-pricing.md) | Ore reprocess pricing as a pricing-rule option | Accepted |
| [0027](0027-deploy-coolify.md) | Deploy on Coolify with a managed PostgreSQL | Accepted |
| [0028](0028-esi-market-source-and-aggregation.md) | ESI market source + aggregation for non-Fuzzwork hubs | Accepted |
| [0029](0029-encrypted-refresh-token-structures.md) | Encrypted refresh token for structure-market access | Accepted |
| [0030](0030-buyback-drop-off-locations.md) | Buyback drop-off locations a member picks per appraisal | Accepted |
| [0031](0031-per-rule-market-hub.md) | Per-rule market-hub override | Accepted |
| [0032](0032-automated-version-bump.md) | Automated version bump on merge | Accepted |
| [0033](0033-pluggable-cache.md) | Pluggable cache (L1) in front of the market_prices DB cache | Accepted |
| [0034](0034-background-market-refresh.md) | Background auto-refresh of non-Fuzzwork market prices | Accepted |
| [0035](0035-esi-overload-protection.md) | ESI-overload protection: per-appraisal type cap + global concurrency cap | Accepted |
| [0036](0036-corp-roster-manager-designation.md) | One Corp ESI access token (structure markets + corp roster); manager designation (amends 0029) | Accepted |
| [0037](0037-corp-contract-watcher.md) | Corp contract watcher → validated appraisal status (separate mutable link table) | Accepted |
| [0038](0038-open-in-eve-login-token.md) | "Open in EVE" via a session-held login refresh token (amends 0004) | Accepted |
| [0039](0039-custom-rule-folders.md) | Custom folders for pricing rules (emergent free-text label + "Group by" toggle) | Accepted |
| [0040](0040-appraisal-link-unfurl-preview.md) | Server-rendered link-unfurl preview for shared appraisals (public OG meta: value + location) | Accepted |
| [0041](0041-app-admin-authorization-axis.md) | App-admin authorization axis (env-var allowlist, orthogonal to corp roles) | Proposed |
| [0042](0042-paid-accounting-entitlements.md) | Paid accounting add-on: hosted-only per-corp entitlements + ISK payment reconciliation | Proposed |
| [0043](0043-lot-based-buyback-accounting.md) | Lot-based buyback accounting (FIFO lots from completed contracts, cost basis, NRV) | Proposed |
| [0044](0044-hangar-inventory-reconciliation.md) | Hangar inventory reconciliation (ESI corp assets vs derived idle stock) | Proposed |
| [0045](0045-esi-sales-ingestion-and-manual-entry.md) | ESI outgoing-sales ingestion + manual-entry escape hatch | Proposed |
| [0046](0046-playwright-e2e-smoke.md) | E2E smoke tests: Playwright against the single deployable (minted sessions, `buyback_e2e` DB) | Proposed |

See the project [architecture overview](../architecture.md) for how these fit together.
