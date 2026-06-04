# 0012. Single deployable; backend serves the SPA

- **Status:** Accepted
- **Date:** 2026-06-04

## Context

The target operator is a small corp that wants minimal moving parts. In
development, though, we want the Vite dev server with HMR. We need a packaging
model that's trivial to self-host yet pleasant to develop.

## Decision

Ship a **single deployable**: in production the FastAPI app **serves the built SPA**
static assets (and SPA-history fallback) alongside `/api/v1`. In development the
**Vite dev server** runs separately and **proxies** `/api` to the backend.
Configuration (SSO client id/secret, DB URL, hub, session secret) comes from
**environment variables** via `pydantic-settings`, with a committed `.env.example`.

## Consequences

- One origin in production → no CORS config and the session cookie "just works".
- One process + one SQLite file to run; a `Dockerfile` can bundle the built
  frontend and the backend for one-command self-hosting.
- The frontend build output must be produced before/within the backend image build;
  document the order.
- Dev and prod differ in how the SPA is served (proxy vs static) — keep the API base
  URL configurable (`VITE_API_BASE_URL`) to bridge them.

## Alternatives considered

- **Separate frontend host (CDN/static host) + API host** — common for SaaS and
  better at scale, but adds CORS, cross-site cookie complexity, and a second deploy
  target; unnecessary for the self-host MVP. The env-driven base URL leaves this
  open for later.
