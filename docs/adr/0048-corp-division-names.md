# 0048. Real corp division names on the Stock page

- **Status:** Accepted
- **Date:** 2026-08-10
- **Amends:** [ADR-0036](0036-corp-roster-manager-designation.md) (another scope folded
  into the one Corp ESI access grant)
- **Relates to:** [ADR-0044](0044-hangar-inventory-reconciliation.md) (the hangar picker
  this labels), [ADR-0045](0045-esi-sales-ingestion-and-manual-entry.md) (the wallet
  picker this labels)

## Context

The Stock page asks managers to pick corp **wallet** and **hangar** divisions by bare
number ("Wallet division 3", "Hangar 2"), but in game those divisions carry the corp's
own names ("Buyback ISK", "Deliveries"). Managers think in the names; the numbers force
a mental mapping and invite off-by-one mistakes in exactly the config that drives money
ingestion (ADR-0045) and stock reconciliation (ADR-0044). ESI exposes the names via
`GET /corporations/{id}/divisions/` behind `esi-corporations.read_divisions.v1` and the
in-game **Director** role.

## Decision

**Fold `esi-corporations.read_divisions.v1` into the Corp ESI access grant and label the
Stock page's wallet/hangar pickers with the corp's real division names — best-effort,
falling back to the generic labels.**

- **One more scope on the one grant** (`eve_corp_divisions_scopes` →
  `eve_corp_token_scopes`), same pattern as contracts (ADR-0037), assets (ADR-0044), and
  wallet/orders (ADR-0045). Existing grants predate it → the Config panel shows the
  standard "reconnect to enable …" hint.
- **Cosmetic, so degrade — never error.** The names only label UI controls; nothing keys
  off them. A 401/403 (missing scope or non-Director character) degrades to empty maps in
  the plugin; a missing/expired token degrades in the use case. The UI then falls back to
  "Wallet division N" / "Hangar N". ESI omits `name` for divisions left at their in-game
  default (the master wallet can never be renamed) — those fall back per division.
- **Manager-gated read** at `GET /corporations/me/accounting/divisions`, beside the
  pickers it serves. Names are fetched live per request (ESI caches them) and never
  persisted — no new stored data, no retention question.

## Consequences

- New scope on the corp token → **Privacy page update** (repo convention) + a one-time
  **reconnect** for existing grants; until then labels stay generic.
- The authorizing character must be a **Director** for names to resolve — already the
  effective bar for the assets read (ADR-0044), so in practice no new requirement.

## Alternatives considered

- **Let managers type custom labels in the app** — a second source of truth that drifts
  from the game; the game already has the names. Rejected.
- **Persist the names and refresh in the background** — a cache + staleness story for a
  label; the live read is one small, ESI-cached call on a config page. Rejected.
