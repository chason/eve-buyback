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
| Persistence | SQLAlchemy 2.0 + Alembic on SQLite       | Postgres-ready (ADR-0002)               |
| Frontend    | TypeScript + React (Vite) + TanStack Query | SPA; types generated from OpenAPI (ADR-0011, 0013) |
| Auth        | EVE SSO → backend session cookie         | No persisted EVE tokens (ADR-0004)      |
| Market data | Fuzzwork aggregates, cached              | (ADR-0006)                              |
| Tooling     | `uv`/`venv` (py), `npm` (front)          | Pin exact tooling once chosen           |

If any of these change, edit this table **and the relevant ADR** so the rest of the
doc stays honest.

## Layout

```
buyback/
├── backend/              # Python API
│   ├── app/
│   │   ├── main.py       # FastAPI app factory + lifespan
│   │   ├── config.py     # pydantic-settings (env, prefix BUYBACK_)
│   │   ├── db.py         # async engine/session + Base
│   │   ├── api/v1/       # routers (health; auth/rules/quote land later)
│   │   └── models/       # SQLAlchemy models (empty until next milestone)
│   ├── alembic/          # migrations (async env.py)
│   └── tests/            # pytest
├── frontend/             # TypeScript SPA (Vite + React + TanStack Query)
│   └── src/{api,}        # fetch wrapper + health view; App.tsx
├── docs/                 # architecture.md + adr/
└── .github/workflows/    # CI
```

## Commands

```bash
# Backend (from backend/)
uv sync --extra dev                      # create venv + install deps
uv run uvicorn app.main:app --reload     # dev server :8000
uv run pytest                            # tests
uv run ruff check .                      # lint
uv run alembic upgrade head              # apply migrations (once models exist)

# Frontend (from frontend/)
npm install
npm run dev                              # Vite dev server :5173 (proxies /api)
npm run build                            # typecheck (tsc) + production build
```

## Conventions

- Keep the API and frontend independently runnable; the frontend reaches the
  backend over HTTP (configure the base URL via env, don't hardcode).
- Match the style of surrounding code. Add tests alongside new behavior.
- Don't commit secrets — use `.env` files (already gitignored).
- A pre-commit hook (`.githooks/pre-commit`) runs backend `ruff check` + `pytest`
  and the frontend typecheck (`tsc`). Enable per clone:
  `git config core.hooksPath .githooks` (needs `uv` and `npm` on PATH).

## Notes for Claude

- This is a fresh project: prefer asking before introducing a major framework or
  dependency not listed above.
- When you scaffold a part of the app, update the **Layout** and **Commands**
  sections in this file in the same change.
