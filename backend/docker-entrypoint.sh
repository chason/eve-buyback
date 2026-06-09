#!/bin/sh
# Container entrypoint: bring the schema up to date, kick off a one-off SDE seed if
# the reference data is missing, then start the server.
# Migrations are idempotent (alembic tracks the applied head), so this is safe to run
# on every boot / restart (ADR-0012, ADR-0024).
set -e

echo "Applying database migrations (alembic upgrade head)…"
alembic upgrade head

# Auto-seed SDE reference data (types, market groups, ore yields, NPC stations) when
# it's missing/incomplete (ADR-0009, ADR-0028). Runs in the background so the app
# serves immediately; `--if-needed` makes it a cheap no-op once seeded, so restarts
# and redeploys don't re-download. A newly added reference table (empty) triggers a
# one-off re-seed. Disable with BUYBACK_AUTO_SEED=0.
if [ "${BUYBACK_AUTO_SEED:-1}" != "0" ]; then
    echo "Auto-seeding SDE reference data if needed (background)…"
    python -m app.sde.seed --if-needed &
fi

exec "$@"
