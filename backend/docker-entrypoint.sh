#!/bin/sh
# Container entrypoint: bring the schema up to date, then start the server.
# Migrations are idempotent (alembic tracks the applied head), so this is safe
# to run on every boot / restart (ADR-0012, ADR-0024).
set -e

echo "Applying database migrations (alembic upgrade head)…"
alembic upgrade head

exec "$@"
