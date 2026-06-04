# buyback

A self-hostable EVE Online corporation **buyback** app: members get priced quotes
for items (e.g. "90% Jita Buy"), Buyback Managers configure pricing, prices come
from Fuzzwork market aggregates.

- **Architecture & decisions:** [`docs/architecture.md`](docs/architecture.md) and
  [`docs/adr/`](docs/adr/).
- **Stack:** Python (FastAPI) API + TypeScript (React/Vite) SPA, SQLite via
  SQLAlchemy/Alembic.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (manages Python + the backend venv)
- [Node.js](https://nodejs.org/) 20+ and npm

## Backend (`backend/`)

```bash
cd backend
uv sync --extra dev          # create venv + install deps (incl. dev tools)
uv run uvicorn app.main:app --reload   # http://127.0.0.1:8000
uv run pytest                # tests
uv run ruff check .          # lint
# migrations (once models exist):
uv run alembic revision --autogenerate -m "message"
uv run alembic upgrade head
```

Health check: <http://127.0.0.1:8000/api/v1/health> · API docs: `/docs`.

## Frontend (`frontend/`)

```bash
cd frontend
npm install
npm run dev                  # http://localhost:5173 (proxies /api -> backend)
npm run build                # typecheck + production build
```

Run the backend and frontend together in dev; the Vite proxy forwards `/api` to
`http://127.0.0.1:8000`. The home page shows the backend health status, proving the
two halves talk.
