# 0025. UUID primary keys for app-owned entities; EVE ids as unique columns

- **Status:** Accepted
- **Date:** 2026-06-07
- **Relates to:** [0024](0024-postgresql-database.md) (Postgres rebuild that carried this), [0022](0022-no-sequential-pks-in-api.md) (no sequential PKs in the API), [0014](0014-persisted-appraisals.md) (`public_id`), [0003](0003-multi-tenant-corp-scoping.md) (tenancy)

## Context

App-owned tables (`characters`, `corporations`, `manager_assignments`,
`buyback_configs`, `pricing_rules`, `appraisals`, `appraisal_lines`) had
auto-increment integer primary keys, and the corp/character tables used the **EVE
natural id as that PK**. Two problems:

- **An EVE id is not ours to use as identity.** It's an external value. Using it as the
  primary key couples row identity to a number CCP controls; if one ever changed (or a
  test/import needed a placeholder), every FK referencing it would be wrong.
- We were rebuilding the database on Postgres ([ADR-0024](0024-postgresql-database.md))
  with no shipped data — a free moment to fix identity without a data migration.

[ADR-0022](0022-no-sequential-pks-in-api.md) already established that **sequential
surrogate PKs must not leak into the API**. That ADR solved it per-resource (rules
addressed by target, appraisals by `public_id`). This ADR addresses the storage side.

## Decision

**App-owned entities get a UUID primary key.** The EVE natural id is demoted to a
plain **unique-constrained column** (`eve_id`), and internal foreign keys reference the
**UUID**, not the EVE id.

Convention, applied uniformly:

- `id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)`.
- The entity's own EVE id → `eve_id` with `unique=True` (still fully looked-up by it;
  the unique constraint indexes it). If it ever changed, the row identity survives.
- FK columns keep the `<entity>_id` name but now hold the referenced row's **UUID**
  (`ForeignKey("corporations.id")`).
- **Denormalized audit-actor fields stay EVE ints with no FK** — `ceo_character_id`,
  `registered_by_character_id`, `granted_by_character_id`, `created_by_character_id`.
  They record "who did this" as an EVE id and don't need referential identity.
- `Appraisal.public_id` (the random external slug, ADR-0014) is unchanged — UUIDs are
  internal; the appraisal's shareable handle stays the slug.

**Scope boundary — these stay EVE-keyed, deliberately:** `SdeType` (PK `type_id`),
`SdeMarketGroup` (PK `market_group_id`), `MarketPrice` (PK `hub_id, type_id`), and
`SdeMetadata` (singleton) are **reference / cache** data keyed by the EVE id by nature;
`PricingRule.target_id` is a **polymorphic EVE-id reference** (a type or market-group
id) and rule resolution walks `market_group_id` parent links — all keep working on EVE
ids. UUID-keying them would buy nothing and break the natural joins.

**The API contract does not change.** DTOs still speak EVE ids (`character_id`,
`corporation_id`, …). Records that the interface maps to those DTO fields source the
value from `eve_id` via a Pydantic `validation_alias`, so the UUID never surfaces and
the **frontend is untouched**.

## Consequences

- **Stable internal identity.** A row's identity is a UUID we own; the EVE id is just an
  attribute (unique, indexed, still the lookup key at the use-case boundary).
- **Use cases resolve EVE id → UUID once, then thread the UUID.** Auth, registration,
  manager, config, rule, and appraisal flows already fetch the corp/character up front;
  they pass `corp.id` / `char.id` to corp-scoped child repositories. `resolve_authenticated_user`
  (per request, ADR-0016) does one extra character lookup by `eve_id` to get its UUID for
  the manager check — the row exists post-login.
- **Repositories never return ORM rows** (ADR-0018 still holds): records expose `id`
  (UUID) and `eve_id`, and where a record maps to an EVE-id DTO field it carries that EVE
  id via join (e.g. `list_managers` joins `characters` for `eve_id` + `name`).
- Storage cost: a UUID PK is wider than a 4-byte int and random UUIDs don't cluster on
  insert. Negligible at this scale, and worth the identity guarantee.

## Alternatives considered

- **Keep EVE ids as PKs** — simplest, but couples row identity to an external value and
  was the status quo we set out to fix on the greenfield rebuild. Rejected.
- **Integer surrogate PK + EVE id unique column** (instead of UUID) — solves the
  identity coupling and is narrower, but a global sequence is exactly the cross-tenant
  enumeration signal [ADR-0022](0022-no-sequential-pks-in-api.md) avoids. A UUID leaks
  nothing if one ever did surface, and we never serialize it anyway. UUID chosen.
- **UUID-key everything including SDE/cache** — uniform but pointless: that data is
  externally keyed by nature and joined on EVE ids. Rejected as scope creep.
