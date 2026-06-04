# 0011. Versioned API + TypeScript types generated from OpenAPI

- **Status:** Accepted
- **Date:** 2026-06-04

## Context

Today the only consumer is our SPA, but the API "might" gain other consumers. We
want the frontend and backend to stay in lockstep without hand-maintaining a
duplicate set of TypeScript interfaces, and we want room to evolve the API without
breaking clients.

## Decision

Treat **FastAPI's auto-generated OpenAPI schema as the contract**. Namespace all
routes under **`/api/v1`** so breaking changes can ship as `/api/v2` later.
Generate the frontend's request/response types from the OpenAPI schema with
**`openapi-typescript`** (a build/codegen step), so the SPA's types are derived,
never hand-written.

## Consequences

- One source of truth; backend DTO changes surface as TypeScript compile errors in
  the frontend after regeneration.
- Versioned paths give a clean deprecation path when external consumers exist.
- Adds a codegen step to the frontend build/dev workflow; document it and check in
  the generated file (or generate in CI).
- Because auth is a session cookie ([ADR-0004](0004-eve-sso-session-auth.md)),
  external/non-browser consumers will later need an additional mechanism (API keys
  or token grant); that will be its own ADR when the need is real.

## Alternatives considered

- **Hand-written TS types** — fast to start, but drift from the backend is
  inevitable and silent.
- **gRPC / GraphQL** — richer contracts, but overkill for a small REST surface and
  heavier for a simple SPA; REST + OpenAPI is sufficient.
- **Generate a full TS client SDK** (not just types) — more than needed for MVP;
  types + `fetch` wrapper ([ADR-0013](0013-frontend-stack.md)) suffices.
