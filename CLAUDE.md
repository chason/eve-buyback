# buyback

A web application with a **Python API backend** and a **TypeScript frontend**.

> This file gives Claude Code the context it needs to work in this repo. Keep it
> short and current — update it as the architecture solidifies.

## Status

🚧 Early scaffolding. The stack below is the intended direction; directories are
created as features land. Update this section as things become real.

## Stack

| Layer    | Choice                          | Notes                                  |
| -------- | ------------------------------- | -------------------------------------- |
| Backend  | Python + FastAPI                | REST/JSON API                          |
| Frontend | TypeScript + React (Vite)       | SPA that talks to the API              |
| Tooling  | `uv`/`venv` (py), `npm` (front) | Pin exact tooling once chosen          |

If any of these change, edit this table first so the rest of the doc stays honest.

## Layout

```
buyback/
├── backend/      # Python API (FastAPI app, models, tests)
└── frontend/     # TypeScript SPA (Vite + React)
```

## Commands

Fill these in as the projects are scaffolded. Expected shape:

```bash
# Backend (from backend/)
uv venv && uv pip install -r requirements.txt   # or pyproject/poetry
uvicorn app.main:app --reload                    # run dev server
pytest                                           # run tests

# Frontend (from frontend/)
npm install
npm run dev                                       # Vite dev server
npm run build
npm test
```

## Conventions

- Keep the API and frontend independently runnable; the frontend reaches the
  backend over HTTP (configure the base URL via env, don't hardcode).
- Match the style of surrounding code. Add tests alongside new behavior.
- Don't commit secrets — use `.env` files (already gitignored).

## Notes for Claude

- This is a fresh project: prefer asking before introducing a major framework or
  dependency not listed above.
- When you scaffold a part of the app, update the **Layout** and **Commands**
  sections in this file in the same change.
