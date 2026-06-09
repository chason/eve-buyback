# 0027. Deploy on Coolify with a managed PostgreSQL

- **Status:** Accepted
- **Date:** 2026-06-09

## Context

The single-deployable image ([ADR-0012](0012-single-deployable-packaging.md)) needs
a home. The operator runs a rented dedicated server with **Coolify** (a self-hosted
PaaS over Docker, with a Traefik reverse proxy terminating TLS on 80/443) and
**Tailscale** for tailnet-only admin. We want the app public on HTTPS, a real
database with backups, and a repeatable, low-ceremony deploy — without standing up
Kubernetes or a bespoke CI/CD pipeline.

## Decision

Deploy buyback as a **Coolify "Docker Compose" application** built from the GitHub
repo's `Dockerfile`, driven by a dedicated **`docker-compose.coolify.yml`**, against
a **Coolify-managed PostgreSQL** resource (not a DB bundled in the compose).

- Ingress is Coolify/Traefik on 80/443; the app exposes only container port 8000 and
  is routed via the `SERVICE_FQDN_APP_8000` magic variable. No host ports are
  published, no new firewall rules are opened.
- The database is a separate managed resource (built-in backups + lifecycle); the app
  reaches it on Coolify's internal network via `BUYBACK_DATABASE_URL`
  (`postgresql+asyncpg://…`).
- Secrets and config (session secret, EVE SSO id/secret/redirect, DB URL) are set as
  Coolify environment variables — never committed.
- Schema migrations run automatically: the image entrypoint runs `alembic upgrade
  head` on every boot. The SDE seed (`python -m app.sde.seed`) is a one-time manual
  post-deploy step.
- The compose used for `docker compose up` self-hosting (`docker-compose.yml`, which
  *does* bundle Postgres and publish a port) is kept separate from the Coolify one,
  since the two topologies differ.

The operator runbook is [`docs/deploy-coolify.md`](../deploy-coolify.md).

## Consequences

- One image, one app resource, one DB resource — managed through the Coolify UI;
  updates are "push to `main` → Deploy" (optionally an auto-deploy webhook).
- Backups and DB lifecycle are Coolify's job, not the app's.
- Two compose files to keep coherent (self-host vs Coolify). The Dockerfile and app
  env contract are shared, which limits drift.
- The app↔DB link depends on a Coolify network step (shared/predefined network); a
  missed step surfaces as connection-refused — called out in the runbook.
- The deploy is **UI-driven** (admin is tailnet-only), so it isn't reproduced in
  this repo's CI; CI only builds the image ([the `docker` job](../../.github/workflows/ci.yml)).

## Alternatives considered

- **Bundle Postgres in the Coolify compose** — fewer Coolify resources, but no managed
  backups/lifecycle and the DB's data volume is tied to the app stack. Rejected in
  favor of the managed DB.
- **Coolify "Dockerfile" application (no compose)** — works, but the compose file lets
  us declare the healthcheck, exposed port, FQDN wiring, and env contract in-repo and
  version them. Chosen the compose.
- **Nixpacks / buildpack auto-detection** — would ignore our tuned multi-stage build
  (SPA + uv) and the migration entrypoint. Rejected; we already have a good Dockerfile.
- **Kubernetes / Nomad / bare `docker compose` + manual Traefik** — far more operational
  surface than a single-corp app warrants; Coolify already provides proxy, TLS, and a UI.
