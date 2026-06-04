# 0003. Multi-tenancy via corp_id row scoping

- **Status:** Accepted
- **Date:** 2026-06-04

## Context

One deployment must be able to serve a single corp (typical self-host) or many
corps (registered tenants) without code changes. EVE corporations have a stable
numeric id, and every domain object (config, rules, managers, appraisals) belongs
to exactly one corp.

## Decision

Adopt **single-database, shared-schema multi-tenancy** keyed by `corp_id` (the EVE
corporation id) as a foreign key / scoping column on every tenant-owned table. The
authenticated session carries the caller's `corp_id`; a data-access layer applies
the `corp_id` filter so a request can only ever read or write its own corp's rows.

## Consequences

- A fresh instance trivially supports one corp and scales to many without schema
  changes — satisfies "multi-tenant capable, self-hostable".
- **Tenant isolation is enforced in code**, not by the database. Centralize
  scoping in shared query helpers / FastAPI deps so no route can forget it; cover
  with tests that attempt cross-corp access.
- Using the real EVE `corp_id` as the key avoids an extra mapping and makes ESI
  cross-checks (CEO, membership) direct.
- Future hardening (per-tenant rate limits, soft-delete, export) layers on top.

## Alternatives considered

- **Schema-per-tenant / database-per-tenant** — stronger isolation, but heavy
  operationally and pointless at MVP scale and on SQLite.
- **Single-tenant only** — simplest, but no path to multiple corps without
  redeploying; rejected per the chosen tenancy model.
