# buyback

A web application with a **Python API backend** and a **TypeScript frontend**.

> This file gives Claude Code the context it needs to work in this repo. Keep it
> short and current — update it as the architecture solidifies.

## Status

🚧 Early scaffolding. Architecture is decided but code isn't written yet;
directories are created as features land. Update this section as things become real.

**The plan lives in [`docs/architecture.md`](docs/architecture.md); decisions and
their rationale are in [`docs/adr/`](docs/adr/). Read those before making
architectural changes.**

## What this is

A self-hostable, multi-tenant **EVE Online corporation buyback** app: members get
priced quotes for items (e.g. "90% Jita Buy"), Buyback Managers configure pricing
rules, prices come from Fuzzwork market aggregates. See the architecture doc for the
domain model, auth flow, and pricing-rule resolution.

## Stack

| Layer       | Choice                                   | Notes                                   |
| ----------- | ---------------------------------------- | --------------------------------------- |
| Backend     | Python + FastAPI + Pydantic v2 (async)   | REST/JSON API under `/api/v1` (ADR-0001)|
| Persistence | SQLAlchemy 2.0 + Alembic on PostgreSQL (asyncpg) | Sole DB; UUID app-entity PKs (ADR-0024, 0025) |
| Frontend    | TypeScript + React (Vite) + TanStack Query | SPA; types generated from OpenAPI (ADR-0011, 0013) |
| Auth        | EVE SSO → backend session cookie         | No server-side login token; login keeps an encrypted refresh token in the session cookie for "Open in EVE" (ADR-0004, 0038); one encrypted **Corp ESI access** token per corp powers structure markets, the roster used to designate managers, and the contract watcher (ADR-0029, 0036, 0037) |
| Market data | Fuzzwork aggregates, cached; ESI orders for other hubs; background refresh keeps non-Fuzzwork hubs warm; per-appraisal ESI-type cap + global ESI concurrency cap | (ADR-0006, 0028, 0034, 0035) |
| Tooling     | `uv`/`venv` (py), `npm` (front)          | Pin exact tooling once chosen           |

If any of these change, edit this table **and the relevant ADR** so the rest of the
doc stays honest.

## Layout

```
buyback/
├── backend/              # Python API — layered (see "Backend architecture" below)
│   ├── app/
│   │   ├── main.py       # app factory + lifespan; wires middleware, routers, error handlers, background scheduler (ADR-0034)
│   │   ├── config.py     # pydantic-settings (env, prefix BUYBACK_)
│   │   ├── interface/    # INTERFACE: FastAPI routers (v1/) + deps, security, middleware, error mapping, background-job wiring (jobs.py)
│   │   ├── application/  # APPLICATION: use cases (auth, corporations, corp_roster, corp_contracts, structure_tokens, sde, market, market_refresh, reference, pricing, appraisals, locations) + typed errors
│   │   ├── domain/       # DOMAIN: small pure functions (roles, auth helpers, market TTL, pricing/resolution, contract match/status)
│   │   ├── data/         # DATA: db engine, models/, records.py (pydantic), repositories/
│   │   ├── plugins/      # PLUGINS: outside-resource gateways (EVE ESI, SSO, Fuzzwork, SDE source, cache); return pydantic
│   │   ├── schemas/      # API request/response DTOs (interface contracts)
│   │   ├── sde/          # deploy-time SDE seed CLI (python -m app.sde.seed)
│   │   └── openapi_export.py  # writes frontend/openapi.json for TS type-gen (ADR-0011)
│   ├── alembic/          # migrations (async env.py)
│   ├── docker-entrypoint.sh  # container start: alembic upgrade head → uvicorn
│   └── tests/            # pytest
├── frontend/             # TypeScript SPA (Vite + React + TanStack Query); Pico.css (ADR-0023)
│   ├── openapi.json      # exported backend schema (source for gen:api)
│   └── src/              # api/ (+ generated schema.d.ts, types.ts), components/, pages/, lib/, test/
├── e2e/                  # Playwright smoke suite vs the single deployable (ADR-0046)
│   ├── support/          # env resolution, global setup, e2e_setup.py (e2e DB + minted sessions)
│   └── tests/            # browser journeys (small smoke pack — unit suites stay primary)
├── docs/                 # architecture.md + adr/
├── Dockerfile            # single-deployable image: builds SPA, serves it + /api/v1 (ADR-0012)
├── docker-compose.yml    # self-host stack: Postgres + app
├── docker-compose.coolify.yml  # Coolify deploy: app only, managed Postgres, Traefik (ADR-0027)
├── .env.example          # compose config template (copy to .env)
└── .github/workflows/    # CI
```

> Deploying to the Coolify host? Follow [`docs/deploy-coolify.md`](docs/deploy-coolify.md).

## Backend architecture (layers)

The backend is organized into strict layers. **Dependencies point inward/downward
only** — an outer layer may import the one beneath it, never the reverse:

```
interface  →  application  →  domain
                  ↓
                data        plugins
```

- **`interface/`** — the API. FastAPI routers under `v1/` plus session/auth
  dependencies (`security.py`), the CSRF middleware, the DB-session provider
  (`deps.py`), and the `ApplicationError → HTTP status` mapping (`errors.py`).
  Routers contain **only** API concerns (status codes, request/response wiring,
  reading the session cookie) and **call the application layer**. No business logic,
  no database access.
- **`application/`** — **use cases**, one function per user action (e.g.
  `complete_login`, `register_corporation`). A use case orchestrates: it calls
  `plugins/` and `domain/` functions and `data/` repositories, owns the unit of work
  (`session.commit()`), and raises typed `errors.py` exceptions on rule violations.
  It knows nothing about HTTP.
- **`domain/`** — small, **single-purpose pure functions** with no I/O
  (e.g. `derive_role`, `role_at_least`, `generate_pkce`). Use cases compose these.
- **`data/`** — all database logic. `models/` holds SQLAlchemy ORM entities;
  `repositories/` holds query/write functions, kept in **separate files** from the
  models. **Repositories never return ORM entities** — they return the Pydantic
  read-models in `records.py`, so the DB shape never leaks upward.
- **`plugins/`** — gateways to **outside APIs** (EVE ESI, EVE SSO). Pure transport;
  like the data layer, they **return Pydantic models** whenever they hand back data.
- **`schemas/`** — Pydantic **API DTOs** (the request/response contract). The
  interface layer maps `data` records / `application` results to these DTOs; the
  internal model shape and the public API contract stay decoupled.

When you add a feature, add a use case in `application/`, push pure logic down into
`domain/`, put persistence in a `data/` repository (returning a `records.py` model),
and keep the router in `interface/v1/` thin. New outside-API integrations go in
`plugins/`.

Some layers carry their own `CLAUDE.md` with layer-local conventions (currently
`app/data/` and `app/application/`); these load automatically when you work in that
directory. Read them before changing files there.

## Commands

```bash
# One-time: create the Postgres databases (ADR-0024), then set
# BUYBACK_DATABASE_URL in backend/.env. The test suite derives `<name>_test`.
createdb buyback ; createdb buyback_test

# Backend (from backend/)
uv sync --extra dev                      # create venv + install deps
uv run uvicorn app.main:app --reload     # dev server :8000
uv run pytest                            # tests
uv run pytest --cov                      # tests + coverage (greenlet-aware, see pyproject)
uv run ruff check .                      # lint
uv run alembic upgrade head              # apply migrations (once models exist)
uv run python -m app.sde.seed            # seed SDE reference (types, groups, ore reprocess yields, NPC stations) from Fuzzwork (ADR-0009, 0026, 0028)
uv run python -m app.openapi_export      # write frontend/openapi.json for TS type-gen

# Frontend (from frontend/)
npm install
npm run dev                              # Vite dev server :5173 (proxies /api)
npm run gen:api                          # regenerate src/api/schema.d.ts from openapi.json (ADR-0011)
npm run lint                             # ESLint
npm run test                             # Vitest + React Testing Library
npm run build                            # typecheck (tsc) + production build

# E2E smoke suite (from e2e/, ADR-0046) — real browser vs the single deployable.
# Prereqs: frontend built (npm run build), Postgres running; the suite creates and
# owns a `buyback_e2e` database (dropped + recreated per run).
npm install                              # once, then: npx playwright install chromium
npm test                                 # run the smoke pack (starts the server itself)
npm run test:headed                      # same, with a visible browser

# Deploy — single image serves the built SPA + /api/v1 (ADR-0012). From repo root:
cp .env.example .env                     # then fill in secrets (SESSION_SECRET, EVE SSO, POSTGRES_PASSWORD)
docker compose up --build -d             # Postgres + app; entrypoint runs migrations + auto-seeds the SDE on first boot
docker compose exec app python -m app.sde.seed   # force a SDE refresh (auto-seed handles first boot; BUYBACK_AUTO_SEED=0 disables)
docker build -t buyback .                # build the image alone (e.g. for Coolify + managed Postgres)
```

> Regenerate API types after a backend DTO change:
> `uv run python -m app.openapi_export` (in `backend/`) then `npm run gen:api` (in `frontend/`).

## Conventions

- Keep the API and frontend independently runnable; the frontend reaches the
  backend over HTTP (configure the base URL via env, don't hardcode).
- **Versioning is one number, bumped automatically per merged PR.** A GitHub Action
  (`.github/workflows/version-bump.yml`) increments `APP_VERSION` in
  `backend/app/_version.py` on every merge to `main` (ADR-0032). **Do not bump it in
  a PR** — parallel PRs would claim the same number. Served at `/api/v1/version`,
  shown in the UI top bar. This is the whole scheme — no tags or semver.
- Match the style of surrounding code. Add tests alongside new behavior.
- Don't commit secrets — use `.env` files (already gitignored).
- **Token-use changes must update the Privacy page.** Any change to how an EVE token
  is used, stored, scoped, or refreshed — a new scope, a new ESI call made with a token,
  a new persisted token, a change to retention/encryption/revocation — must update the
  user-facing Privacy / Data Use page (`frontend/src/pages/Privacy.tsx`) and its test in
  the **same change**, so the page never describes token handling the app no longer (or
  doesn't yet) do. The page is kept accurate to the token ADRs it cites (ADR-0004, 0029,
  0036, 0037).
- A pre-commit hook (`.githooks/pre-commit`) runs checks for what you stage:
  `backend/` changes → `ruff check` + `pytest`; `frontend/` changes → typecheck
  (`tsc`) + `eslint` + `vitest`. Enable per clone: `git config core.hooksPath .githooks` (needs `uv` and
  `npm` on PATH).

## Notes for Claude

- This is a fresh project: prefer asking before introducing a major framework or
  dependency not listed above.
- When you scaffold a part of the app, update the **Layout** and **Commands**
  sections in this file in the same change.
