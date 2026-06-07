# 0022. Don't expose sequential surrogate PKs in the API

- **Status:** Accepted
- **Date:** 2026-06-07
- **Relates to:** [0003](0003-multi-tenant-corp-scoping.md) (tenancy), [0007](0007-pricing-rule-taxonomy.md) (rule targeting), [0014](0014-persisted-appraisals.md) (appraisal `public_id`)

## Context

Most identifiers the API exposes are **EVE-assigned natural ids** (`character_id`,
`corporation_id`, `type_id`, `market_group_id`, …) — externally meaningful and
correct to surface. Appraisals follow [ADR-0014](0014-persisted-appraisals.md): the
internal integer PK stays hidden behind a random `public_id` (they need a synthetic
handle because two appraisals aren't distinguishable by content and the id is shared
as a link).

An audit found one resource breaking the rule: **`PricingRule`** exposed its
auto-increment integer PK in `RuleOut` and in the `PATCH/DELETE
/corporations/me/rules/{id}` URLs. Tenant isolation was enforced (a foreign rule
returns `404`, so there is **no IDOR**), but a single global sequence leaks low-value
cross-tenant metadata: a corp seeing `id: 4821` can infer roughly how many rules exist
system-wide and the relative creation order across tenants — the enumeration ADR-0014
set out to avoid.

## Decision

**No externally-referenced resource exposes a sequential surrogate PK.**

Unlike an appraisal, a pricing rule has a **natural unique key**: there is at most one
rule per `(corporation_id, target_kind, target_id)` ([ADR-0007](0007-pricing-rule-taxonomy.md)).
So it needs no synthetic handle at all — it's a **singleton resource addressed by its
target**, and we model writes accordingly:

- `RuleOut` carries `target_kind` + `target_id` (no id of any kind).
- `PUT /corporations/me/rules/{target_kind}/{target_id}` is an **idempotent
  create-or-replace** (the body is the full rule minus the target). `201` on create,
  `200` on replace — no `409`, and no write-time `404`. This suits a singleton-per-
  target far better than `POST`-create + `PATCH`-update, and is retry-safe.
- `DELETE /corporations/me/rules/{target_kind}/{target_id}` removes it (`404` if absent).
- The corp-scoped lookup `(corp, target_kind, target_id)` doubles as the tenancy check
  — a foreign rule is simply not found.
- The upsert is a **portable get-then-set** (like `buyback_config.upsert_config`), not a
  dialect-specific `ON CONFLICT`; it runs unchanged on SQLite and PostgreSQL.

The integer PK is retained internally for relationships and ordering, never serialized.

Natural EVE ids remain exposed as before — this ADR is about **synthetic** keys only.

## Consequences

- No cross-tenant volume/timing inference; consistent with ADR-0014's intent.
- No surrogate, public or private, leaves the data layer for rules — and no extra
  column, migration, or id generator is needed. The URL is meaningful
  (`/rules/type/34`, `/rules/market_group/1857`).
- A new externally-addressable resource should be keyed by a **natural id** where one
  exists (like rules); only invent a random `public_id` (see `domain/ids.py`) when
  there is no natural key and/or the handle is shared as a link (like appraisals).
  Reviewers should reject raw integer-PK exposure.

## Alternatives considered

- **Random `public_id` slug on the rule** (like appraisals) — consistent with the
  existing idiom and keeps a one-segment URL, but invents a "public" handle for a
  resource that is never shared and already has a natural key, and costs a column +
  migration + generator. Target-addressing is lighter and more honest; chosen instead.
- **Leave the integer PK exposed** — access-controlled and low severity, but
  inconsistent with ADR-0014 and a needless metadata leak; rejected.
- **Per-corp local sequence / UUID PK** — a local sequence adds insert-time counting
  and races; a UUID works but is heavier than addressing by the target the rule
  already names.
