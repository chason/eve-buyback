# 0007. Pricing rules on EVE market groups + type overrides + global default

- **Status:** Accepted
- **Date:** 2026-06-04

## Context

Managers set buyback values at varying granularity: a **global** default, a
**group** (e.g. Ore), a **subtype** (e.g. Moon Ores), or an **individual item**.
EVE offers two taxonomies: the SDE inventory tree (category → group → type) and the
**market group** tree used by the in-game market browser. The user's "Ore → Moon
Ores → specific ore" mental model maps cleanly onto market groups, which are
hierarchical to arbitrary depth.

## Decision

Express pricing rules over **EVE market groups** plus per-**type** overrides, with
the corp's `BuybackConfig` providing the **global** default. A `PricingRule` has
`target_kind ∈ {market_group, type}`, a `target_id`, an optional `basis`, and a
`percentage`. Resolution for a `type_id` is **most-specific-wins**:

1. a `type` rule for that exact type, else
2. the **nearest ancestor market group** (walking `SdeMarketGroup.parent_id` up
   from the type's `market_group_id`) that has an enabled rule, else
3. the global default in `BuybackConfig`.

## Consequences

- "Group" and "subtype" are just market-group nodes at different depths — no
  special-casing of ore vs moon ore.
- Requires the market-group hierarchy and type→market_group mapping locally
  ([ADR-0009](0009-sde-reference-data.md)).
- Resolution is a cheap upward walk; cache each type's resolved rule per config
  version if needed.
- Items without a `market_group_id` (a few unpublished/special types) fall through
  to the global default — acceptable, and surfaced in the rule editor.

## Alternatives considered

- **SDE inventory category/group** — "moon ore" is not a single clean node there;
  flatter and a worse fit for the product's mental model.
- **Flexible targeting (market group OR inventory group OR type)** — most powerful
  but adds resolution ambiguity (which taxonomy wins?) and UI complexity; deferred.
  The schema (`target_kind`) can grow to accommodate it later without migration pain.
