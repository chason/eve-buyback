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
  - Scopes: `publicData` and `esi-characters.read_corporation_roles.v1`. **Add
    `esi-markets.structure_markets.v1` only if you'll price at player
    structures** (ADR-0029); NPC-station hubs need nothing more.
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

The managed DB and the compose app start on **separate Docker networks**, so the app
can't resolve the DB's hostname until they share one. (A Docker Compose app does
**not** show the "Connect to Predefined Network" UI toggle — networking is defined in
the compose file instead.)

`docker-compose.coolify.yml` already attaches the app to the external **`coolify`**
network (Coolify's shared predefined network) for exactly this reason. You just need
the managed Postgres to be on that same network. Confirm it on the server (tailnet
SSH):

```bash
# Find the Postgres container, then list its networks — `coolify` should appear.
docker ps --format '{{.Names}}' | grep -i postgres
docker inspect <postgres-container> -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
```

- If `coolify` is listed → you're set; use the DB's internal host (step 1) in the URL.
- If it's **not** listed → on the Postgres resource enable **Connect To Predefined
  Network** (under the DB's settings) and redeploy the DB, then re-check.
- If your predefined network has a **different name**, change `coolify` in
  `docker-compose.coolify.yml` (the `networks:` block and the app's `networks:` list)
  to match, and redeploy the app.

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
| `BUYBACK_TOKEN_ENCRYPTION_KEY` | Optional — **only** to price at player structures (ADR-0029): a Fernet key encrypting the stored EVE refresh token. `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Until set, structure authorization is refused; NPC hubs are unaffected. |

`BUYBACK_ENVIRONMENT=production` is already set in the compose (forces secure
cookies and the session-secret check).

## 6. Deploy

Click **Deploy**. On boot the container entrypoint runs `alembic upgrade head`
against the managed DB (idempotent), then starts uvicorn. Watch the deploy logs for
`Applying database migrations…` followed by `Uvicorn running`. Coolify marks the app
healthy via the container healthcheck hitting `/api/v1/health`.

## 7. SDE reference data (auto-seeded)

The container **auto-seeds** the SDE reference data (types, market groups, ore
reprocessing yields, NPC stations) in the background on first boot — the entrypoint
runs `python -m app.sde.seed --if-needed`, which downloads from Fuzzwork only when
the data is missing/incomplete and is a cheap no-op thereafter. So there's **no
manual step** on a fresh deploy; the data fills in within a minute or two (until then
appraisals show "Unknown item"). A newly added reference table also triggers a
one-off re-seed automatically.

Disable with `BUYBACK_AUTO_SEED=0`. To force a refresh (e.g. after a major EVE
expansion), run it manually from the app's **Terminal**:

```bash
python -m app.sde.seed
```

## 8. Verify

- `https://<your-domain>/api/v1/health` → `{"status":"ok","database":"ok"}`.
- `https://<your-domain>/` → the SPA loads; a deep link (e.g. an appraisal URL)
  loads too (SPA history fallback).
- Click **Log in with EVE** → SSO round-trips back and you land authenticated.
  (If the callback errors, the redirect URI / EVE registration / domain don't match.)

## Updating

Push to `main` (or your chosen branch) and **Deploy** again — or enable Coolify's
auto-deploy webhook on the repo. Migrations run automatically on each boot, and the
SDE auto-seeds when incomplete (a new reference table triggers a one-off re-seed); an
already-seeded DB is left as-is, so redeploys don't re-download.

## Troubleshooting

- **`socket.gaierror: Temporary failure in name resolution` (at boot, during the
  migration step):** the app can't resolve the DB hostname — the app and DB aren't on
  a shared network (step 4). Confirm Postgres is on the `coolify` network and the app
  compose attaches to it. This is a *DNS* failure, distinct from `Connection refused`
  (network OK, wrong host/port) and `password authentication failed` (network OK,
  bad creds).
- **Wrong driver:** `BUYBACK_DATABASE_URL` must start with `postgresql+asyncpg://`,
  not `postgres://` — but note a wrong scheme raises a SQLAlchemy *dialect* error,
  not a name-resolution error.
- **Boots then exits with a session-secret error:** `BUYBACK_SESSION_SECRET` is
  empty or the dev placeholder.
- **EVE login bounces / "invalid redirect":** `BUYBACK_EVE_REDIRECT_URI` ≠ the EVE
  app's callback URL, or ≠ your actual domain.
- **Everything is "Unknown item":** SDE not seeded (step 7).
- **Coolify dashboard unreachable over Tailscale:** unrelated to this app — see the
  `ufw route` / container-port gotcha in the deploy-server notes.
