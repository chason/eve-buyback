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
| Auth        | EVE SSO → backend session cookie         | No persisted EVE tokens (ADR-0004)      |
| Market data | Fuzzwork aggregates, cached              | (ADR-0006)                              |
| Tooling     | `uv`/`venv` (py), `npm` (front)          | Pin exact tooling once chosen           |

If any of these change, edit this table **and the relevant ADR** so the rest of the
doc stays honest.

## Layout

```
buyback/
├── backend/              # Python API — layered (see "Backend architecture" below)
│   ├── app/
│   │   ├── main.py       # app factory + lifespan; wires middleware, routers, error handlers
│   │   ├── config.py     # pydantic-settings (env, prefix BUYBACK_)
│   │   ├── interface/    # INTERFACE: FastAPI routers (v1/) + deps, security, middleware, error mapping
│   │   ├── application/  # APPLICATION: use cases (auth, corporations, sde, market, reference, pricing, appraisals) + typed errors
│   │   ├── domain/       # DOMAIN: small pure functions (roles, auth helpers, market TTL, pricing/resolution)
│   │   ├── data/         # DATA: db engine, models/, records.py (pydantic), repositories/
│   │   ├── plugins/      # PLUGINS: outside-API gateways (EVE ESI, SSO, Fuzzwork, SDE source); return pydantic
│   │   ├── schemas/      # API request/response DTOs (interface contracts)
│   │   ├── sde/          # deploy-time SDE seed CLI (python -m app.sde.seed)
│   │   └── openapi_export.py  # writes frontend/openapi.json for TS type-gen (ADR-0011)
│   ├── alembic/          # migrations (async env.py)
│   └── tests/            # pytest
├── frontend/             # TypeScript SPA (Vite + React + TanStack Query); Pico.css (ADR-0023)
│   ├── openapi.json      # exported backend schema (source for gen:api)
│   └── src/              # api/ (+ generated schema.d.ts, types.ts), components/, pages/, lib/, test/
├── docs/                 # architecture.md + adr/
└── .github/workflows/    # CI
```

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
uv run python -m app.sde.seed            # seed SDE reference (types, groups, ore reprocess yields) from Fuzzwork (ADR-0009, 0026)
uv run python -m app.openapi_export      # write frontend/openapi.json for TS type-gen

# Frontend (from frontend/)
npm install
npm run dev                              # Vite dev server :5173 (proxies /api)
npm run gen:api                          # regenerate src/api/schema.d.ts from openapi.json (ADR-0011)
npm run lint                             # ESLint
npm run test                             # Vitest + React Testing Library
npm run build                            # typecheck (tsc) + production build
```

> Regenerate API types after a backend DTO change:
> `uv run python -m app.openapi_export` (in `backend/`) then `npm run gen:api` (in `frontend/`).

## Conventions

- Keep the API and frontend independently runnable; the frontend reaches the
  backend over HTTP (configure the base URL via env, don't hardcode).
- Match the style of surrounding code. Add tests alongside new behavior.
- Don't commit secrets — use `.env` files (already gitignored).
- A pre-commit hook (`.githooks/pre-commit`) runs checks for what you stage:
  `backend/` changes → `ruff check` + `pytest`; `frontend/` changes → typecheck
  (`tsc`) + `eslint` + `vitest`. Enable per clone: `git config core.hooksPath .githooks` (needs `uv` and
  `npm` on PATH).

## Notes for Claude

- This is a fresh project: prefer asking before introducing a major framework or
  dependency not listed above.
- When you scaffold a part of the app, update the **Layout** and **Commands**
  sections in this file in the same change.
