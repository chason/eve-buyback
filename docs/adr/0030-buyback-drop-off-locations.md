# 0030. Accepted buyback drop-off locations

- **Status:** Accepted
- **Date:** 2026-06-09
- **Relates to:** [ADR-0014](0014-persisted-appraisals.md) (immutable snapshots),
  [ADR-0028](0028-esi-market-source-and-aggregation.md) / [ADR-0029](0029-encrypted-refresh-token-structures.md)
  (the pricing hub, string location ids)

## Context

An appraisal records **where prices come from** (`market_hub_id`) but nothing about
**where the goods are physically delivered**. Members hand over bought-back items by
making an in-game contract to the corp at some station or structure; Buyback Managers
want to define the set of accepted **drop-off locations** and have members choose one
when getting an appraisal, so it's clear where each lot will land. This is a logistics
concern, entirely **independent of the pricing hub** (a corp may price off Jita yet
drop off at its own structure).

## Decision

Add a corp-scoped, manager-managed list of accepted drop-off locations, and snapshot
the chosen one onto each appraisal.

- **`buyback_locations`** — one row per `(corp, location)`. A location is an **NPC
  station** (validated against the seeded SDE, which resolves its name + system) or a
  **player structure** (no SDE — the name comes from the structure search, ADR-0029,
  and only the numeric id is validated). The EVE id is a **string** to hold 64-bit
  structure ids, matching `market_hub_id` (ADR-0029). Reuses the existing station and
  structure search; adding is **idempotent**.
- **Manager-gated CRUD.** `GET /corporations/me/locations` is readable by any member
  (they pick one); `POST` / `DELETE` require a Buyback Manager / CEO (`require_role`).
  A dedicated **Locations** page manages the list, separate from pricing Config/Rules.
- **Every appraisal always carries a pickup location.** If the corp has configured ≥1
  location, the member **must pick one of them** (`DeliveryLocationRequired` /
  `DeliveryLocationInvalid`, both 422). If the corp has configured **none**, the
  appraisal **defaults to the market-hub station** (`config.market_hub_id` + name).
  There is no "no location" appraisal.
- **Snapshot, not a foreign key.** The appraisal stores `delivery_location_id` +
  `delivery_location_name` captured at create time (ADR-0014). Removing or renaming a
  location never mutates past appraisals, and the snapshot survives even if the SDE or
  the structure later becomes unresolvable.

## Consequences

- A new corp-scoped table and four endpoints; the appraisal snapshot gains two
  (nullable) columns. Pre-feature appraisals have a null drop-off and render as "—".
- Structure drop-offs depend on the corp having authorized structure access (ADR-0029);
  until then the Locations page points the manager at Config to authorize. NPC-station
  drop-offs need nothing extra.
- The drop-off is **descriptive logistics metadata** — it does not affect pricing,
  acceptance, or the contract recipient (still the corp).

## Alternatives considered

- **Free-text location labels** — simplest and covers any place, but loses validation
  and consistent names; rejected in favor of validated station/structure picks.
- **NPC stations only** — easy (SDE-backed) but a corp headquartered in an Upwell
  structure couldn't list its real drop-off; rejected.
- **Foreign-key the appraisal to the location row** — breaks ADR-0014 immutability
  (a deleted/renamed location would change historical appraisals); rejected for the
  snapshot.
- **Optional selection** — rejected; the user wanted every appraisal to name a pickup,
  with the market hub as the sensible default when no list exists.
