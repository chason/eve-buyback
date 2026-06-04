# 0009. Seed a subset of the EVE SDE

- **Status:** Accepted
- **Date:** 2026-06-04

## Context

Rule resolution ([ADR-0007](0007-pricing-rule-taxonomy.md)) needs the market-group
hierarchy and each type's market group locally; quoting needs name↔`type_id`
lookups and item volume. Resolving all of this from ESI on demand would be slow and
chatty, and the data is effectively static between EVE expansions.

## Decision

**Seed a curated subset of the EVE Static Data Export** into the app database at
deploy time:

- `SdeType` — `type_id`, `name`, `group_id`, `market_group_id`, `volume`,
  `published`.
- `SdeMarketGroup` — `market_group_id`, `parent_id`, `name`.

Import via `backend/app/sde/seed.py` from **Fuzzwork's SDE conversions** (CSV/
SQLite). Version-stamp each import; re-run the seed when CCP ships a new SDE.

## Consequences

- Fast, offline-capable resolution and search; no per-quote ESI calls for taxonomy.
- Adds a periodic maintenance step (re-seed per expansion); document it and make the
  seed idempotent/upsert-based.
- Keeps the DB small by importing only the needed columns/tables, not the full SDE.
- Seed data is reference, not tenant data — shared across all corps, no `corp_id`.

## Alternatives considered

- **Resolve via ESI on demand + cache** — avoids the seed step but is slow on cold
  cache, rate-limited, and ill-suited to walking the market-group tree.
- **Bundle the full SDE** — unnecessary size and import time; we need a few tables.
- **Ship Fuzzwork's SDE SQLite as a second database** — workable, but folding the
  few needed tables into our DB keeps queries and migrations uniform.
