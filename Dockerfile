# syntax=docker/dockerfile:1
#
# Single deployable (ADR-0012): one image that serves /api/v1 and the built SPA.
# Stage 1 compiles the frontend with Node; stage 2 runs the FastAPI backend and
# serves stage 1's static assets. Build from the repo root:
#   docker build -t buyback .

# ---- Stage 1: build the SPA ----
FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend

# Install deps first (cached until the lockfile changes).
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Build → /frontend/dist (tsc typecheck + vite build).
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: backend runtime (Python + uv), serves API + SPA ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime
WORKDIR /app/backend

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    BUYBACK_ENVIRONMENT=production \
    BUYBACK_STATIC_DIR=/app/frontend/dist

# Install Python deps first for layer caching (lockfile-pinned, prod only).
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Backend source + the SPA compiled in stage 1.
COPY backend/ ./
COPY --from=frontend /frontend/dist /app/frontend/dist

EXPOSE 8000
# Entrypoint applies DB migrations, then runs the CMD (uvicorn).
ENTRYPOINT ["sh", "/app/backend/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
