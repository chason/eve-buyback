# 0022. Don't expose sequential surrogate PKs in the API

- **Status:** Accepted
- **Date:** 2026-06-07
- **Relates to:** [0003](0003-multi-tenant-corp-scoping.md) (tenancy), [0014](0014-persisted-appraisals.md) (appraisal `public_id`)

## Context

Most identifiers the API exposes are **EVE-assigned natural ids** (`character_id`,
`corporation_id`, `type_id`, `market_group_id`, …) — externally meaningful and
correct to surface. Appraisals already follow [ADR-0014](0014-persisted-appraisals.md):
the internal integer PK stays hidden behind a random `public_id`.

An audit found one resource breaking that rule: **`PricingRule`** exposed its
auto-increment integer PK in `RuleOut` and in the `PATCH/DELETE
/corporations/me/rules/{id}` URLs. Tenant isolation was still enforced (the use case
returns `404` for a rule belonging to another corp, so there is **no IDOR**), but a
single global sequence leaks low-value cross-tenant metadata: a corp seeing `id: 4821`
can infer roughly how many rules exist system-wide and the relative creation order
across tenants — exactly the enumeration ADR-0014 set out to avoid.

## Decision

**No externally-referenced resource exposes a sequential surrogate PK.** Resources
keyed by an internal auto-increment id get a random, non-enumerable `public_id`
(12-char base64url slug, 72 bits — `domain/ids.py`); the integer PK stays internal,
used only for relationships and ordering.

Applied to `PricingRule`: added a unique `public_id` column; `RuleOut.public_id` and
the rule URLs now use it; lookups for update/delete go through `public_id` with the
existing corp-ownership check. The convention now matches appraisals.

Natural EVE ids remain exposed as before — this ADR is about **synthetic** keys only.

## Consequences

- No cross-tenant volume/timing inference via rule ids; consistent with ADR-0014.
- A new externally-addressable table should either be keyed by a natural id or carry a
  `public_id` (see `generate_*_id` in `domain/ids.py`). Reviewers should reject raw
  integer-PK exposure.
- Slightly more indirection (lookup by `public_id` rather than PK), negligible at this
  scale. The integer PK is retained for FKs/joins.

## Alternatives considered

- **Address rules by their natural target** (`/rules/{target_kind}/{target_id}`, since
  `(corp, target_kind, target_id)` is unique) — elegant and needs no new column, but
  changes the URL shape to two segments and diverges from the established `public_id`
  idiom; the appraisal pattern is the more consistent, lower-surprise choice.
- **Leave it** — access-controlled, low severity, but inconsistent with ADR-0014 and a
  needless metadata leak; rejected.
- **Per-corp local sequence / UUID PK** — local sequence adds insert-time counting and
  races; a UUID works but yields uglier handles than the short slug for no gain.
