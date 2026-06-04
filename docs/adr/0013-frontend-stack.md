# 0013. Frontend: React + Vite + TanStack Query

- **Status:** Accepted
- **Date:** 2026-06-04

## Context

The SPA must handle the SSO redirect dance, fetch corp/config/quote data from our
API, and give managers an editor for pricing rules. We want fast dev iteration,
typed data access, and sane server-state caching without a heavy framework.

## Decision

Build the frontend with **React + Vite + TypeScript**. Use **TanStack Query** for
server-state (fetching, caching, invalidation of corp config, rules, quotes) and
**React Router** for navigation. Data types come from the generated OpenAPI types
([ADR-0011](0011-api-contract-and-typescript-types.md)) consumed through a thin
`fetch` wrapper (`credentials: "include"` for the session cookie); no bespoke
client SDK. UI component library is deferred / left open.

## Consequences

- Vite gives fast HMR and a simple proxy to the backend in dev
  ([ADR-0012](0012-single-deployable-packaging.md)).
- TanStack Query removes most hand-rolled loading/caching/refetch logic and pairs
  well with cache invalidation after manager edits.
- Staying close to the platform (`fetch` + generated types) keeps the dependency
  surface small and the API contract authoritative.
- A component library can be adopted later without disturbing data access.

## Alternatives considered

- **Next.js** — SSR/routing batteries included, but we want a pure SPA against a
  separate API; its server features are unused weight here.
- **Redux / Zustand for server state** — manual cache/refetch handling that
  TanStack Query does better; reserve a small client-state store only if needed.
- **Hand-written fetch hooks** — fine initially but reinvents caching/invalidation.
