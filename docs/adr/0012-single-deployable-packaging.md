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
- A multi-stage `Dockerfile` at the repo root bundles both: stage 1 (Node) runs
  `npm run build` → `frontend/dist`; stage 2 (Python + `uv`) installs the backend
  and copies that `dist` in. The backend mounts it under `/` via
  `SpaStaticFiles` (history fallback to `index.html`) when `BUYBACK_STATIC_DIR`
  points at an existing directory — set in the image, empty in dev so the mount is
  a no-op. `/api/v1` is registered first and keeps priority.
- The frontend build must precede the backend stage — the Dockerfile encodes this
  ordering, so a plain `docker build .` is correct regardless of host state.
- The container entrypoint runs `alembic upgrade head` before starting uvicorn
  (idempotent; safe on every boot). The Postgres DB (ADR-0024) is a separate
  service — bundled in `docker-compose.yml` for self-host, or external/managed when
  only the image is deployed (e.g. Coolify). The entrypoint then **auto-seeds the SDE
  in the background** via `python -m app.sde.seed --if-needed` — a cheap no-op once
  the data is present, so the app serves immediately and restarts don't re-download
  (disable with `BUYBACK_AUTO_SEED=0`).
- Dev and prod differ in how the SPA is served (Vite proxy vs static mount) — keep
  the API base URL configurable (`VITE_API_BASE_URL`) to bridge them.

## Alternatives considered

- **Separate frontend host (CDN/static host) + API host** — common for SaaS and
  better at scale, but adds CORS, cross-site cookie complexity, and a second deploy
  target; unnecessary for the self-host MVP. The env-driven base URL leaves this
  open for later.
