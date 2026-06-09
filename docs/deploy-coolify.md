# Deploying buyback on Coolify

This is the operator runbook for deploying buyback to the Coolify host (see
[ADR-0027](adr/0027-deploy-coolify.md) for the decision and
[ADR-0012](adr/0012-single-deployable-packaging.md) for the single-image model).

**Topology:** one app container (FastAPI serving the built SPA + `/api/v1`) behind
Coolify's Traefik proxy on 80/443, talking to a **Coolify-managed PostgreSQL**
resource. The app image is built from the repo's `Dockerfile`; the deploy is driven
by `docker-compose.coolify.yml`.

> Admin (the Coolify UI, SSH) is **tailnet-only**; the deployed app is public on
> 80/443. None of the steps below open new firewall ports — Traefik already owns
> 80/443 and the managed DB stays on Coolify's internal Docker network.

---

## 0. Prerequisites

- **DNS:** an `A` record for your chosen hostname (e.g. `buyback.example.com`)
  pointing at the server's **public** IP.
- **EVE application:** register one at <https://developers.eveonline.com/> →
  *Manage Applications* → *Create New Application*.
  - Connection type: **Authentication Only**.
  - Scopes: `publicData` and `esi-characters.read_corporation_roles.v1`.
  - **Callback URL:** `https://<your-domain>/auth/callback` (exact match; set it
    after you've fixed the domain in step 3). Note the **Client ID** and **Secret**.

## 1. Create the managed PostgreSQL

In Coolify: **+ New → Database → PostgreSQL** (v17). Create it in the same project
you'll put the app in. After it starts, open the database and copy its **internal**
connection URL — it looks like:

```
postgres://<user>:<password>@<db-internal-host>:5432/<dbname>
```

You'll convert this to the asyncpg form in step 4. (Keep the DB **internal** — do
not expose its public port; the ufw-docker default already denies it.)

## 2. Create the application

**+ New → Application → Public/Private Repository** → pick `chason/eve-buyback`,
branch `main`. Build settings:

- **Build Pack:** Docker Compose
- **Docker Compose Location:** `/docker-compose.coolify.yml`
- Base directory: `/` (repo root)

## 3. Set the domain

In the application's **Domains**, set `https://<your-domain>` (matching your DNS and
the EVE callback). Coolify provisions a Let's Encrypt cert via Traefik and routes the
domain to the app's container port **8000** (wired by the `SERVICE_FQDN_APP_8000`
variable in the compose). Go back and set the EVE app's **Callback URL** to
`https://<your-domain>/auth/callback` if you hadn't.

## 4. Connect the app to the database

The managed DB and the compose app are separate resources, so put them on a shared
network: on the **application** settings enable **Connect to Predefined Network**
(and/or keep both resources in the same Coolify project). Then reference the DB by
its internal host from step 1.

## 5. Environment variables

Set these on the **application** → *Environment Variables* (mark the secrets as
such). The compose file reads them by name:

| Variable | Value |
|---|---|
| `BUYBACK_DATABASE_URL` | The step-1 URL **rewritten to asyncpg**: `postgresql+asyncpg://USER:PASS@DB-HOST:5432/DBNAME` (note the `+asyncpg` and that the scheme is `postgresql`, not `postgres`). |
| `BUYBACK_SESSION_SECRET` | A strong random value: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. The app refuses to boot in production with a weak/empty secret. |
| `BUYBACK_EVE_CLIENT_ID` | From the EVE app. |
| `BUYBACK_EVE_CLIENT_SECRET` | From the EVE app (secret). |
| `BUYBACK_EVE_REDIRECT_URI` | `https://<your-domain>/auth/callback` (must equal the EVE registration). |
| `BUYBACK_MARKET_HUB_ID` | Optional; defaults to `60003760` (Jita 4-4). |

`BUYBACK_ENVIRONMENT=production` is already set in the compose (forces secure
cookies and the session-secret check).

## 6. Deploy

Click **Deploy**. On boot the container entrypoint runs `alembic upgrade head`
against the managed DB (idempotent), then starts uvicorn. Watch the deploy logs for
`Applying database migrations…` followed by `Uvicorn running`. Coolify marks the app
healthy via the container healthcheck hitting `/api/v1/health`.

## 7. Seed the SDE reference data (one-time)

Until this runs, every appraisal rejects every line as "Unknown item". Open the
application's **Terminal** (or *Execute Command*) in Coolify and run:

```bash
python -m app.sde.seed
```

This pulls ~18k types, market groups, and ore reprocessing data from Fuzzwork
(takes a couple of minutes). Re-run after a major EVE expansion to refresh.

## 8. Verify

- `https://<your-domain>/api/v1/health` → `{"status":"ok","database":"ok"}`.
- `https://<your-domain>/` → the SPA loads; a deep link (e.g. an appraisal URL)
  loads too (SPA history fallback).
- Click **Log in with EVE** → SSO round-trips back and you land authenticated.
  (If the callback errors, the redirect URI / EVE registration / domain don't match.)

## Updating

Push to `main` (or your chosen branch) and **Deploy** again — or enable Coolify's
auto-deploy webhook on the repo. Migrations run automatically on each boot. The SDE
seed is **not** re-run automatically.

## Troubleshooting

- **DB connection refused / name resolution:** the app isn't on the DB's network
  (step 4), or `BUYBACK_DATABASE_URL` uses `postgres://` instead of
  `postgresql+asyncpg://`.
- **Boots then exits with a session-secret error:** `BUYBACK_SESSION_SECRET` is
  empty or the dev placeholder.
- **EVE login bounces / "invalid redirect":** `BUYBACK_EVE_REDIRECT_URI` ≠ the EVE
  app's callback URL, or ≠ your actual domain.
- **Everything is "Unknown item":** SDE not seeded (step 7).
- **Coolify dashboard unreachable over Tailscale:** unrelated to this app — see the
  `ufw route` / container-port gotcha in the deploy-server notes.
