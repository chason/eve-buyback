# 0026. Ore reprocess pricing as a pricing-rule option

- **Status:** Accepted
- **Date:** 2026-06-07
- **Relates to:** [0007](0007-pricing-rule-taxonomy.md) (rule resolution), [0006](0006-market-data-fuzzwork.md) (market data), [0009](0009-sde-reference-data.md) (SDE seed), [0021](0021-appraisal-computation-and-storage.md) (appraisal computation), [0020](0020-decimal-money-values.md) (Decimal)

## Context

Corp buyback programs commonly pay for **ore** by its **refined mineral value** rather
than the ore's own market price — you reprocess what you buy, so the minerals are what
it's worth to you. Members need this reflected in their quote.

The buy/sell market price of an ore and the value of its refined minerals can diverge a
lot, and which a corp prefers is a policy choice — and often a *per-ore* one (reprocess
common ores, buy exotic ones directly). So this is configuration, not a fixed rule.

## Decision

Add a **`reprocess` flag to `PricingRule`** (ADR-0007), alongside `basis` and
`percentage`. When the rule that resolves for an item says `reprocess` **and the item is
an ore**, the line is priced by its refined minerals instead of its own market price.

It's a **rule option, not a corp-wide toggle.** Because rule resolution cascades down the
market-group tree (ADR-0007), a single rule on the top-level **Ore** market group
reprocess-prices *every* ore — corp-wide coverage when wanted — while a manager can still
reprocess one ore type or one sub-group. No matching rule (or the corp default) → direct
pricing, exactly as before. The flag is **ignored for non-ores** (they have no refine
yield).

A sibling ore-only rule flag, **`compressed_only`**, rejects the *uncompressed* variants
of matched ores — a line is rejected ("Compressed only") when the resolved rule has the
flag and the ore's name doesn't start with `"Compressed "`. Corps that only buy compressed
ore set it on the relevant ore branch; like `reprocess`, it's ignored for non-ores.

**Pricing model** for an ore quantity `Q` with refine batch size `P` (SDE `portionSize`):

- **Whole refine batches** (`floor(Q / P)`) are valued by their minerals at a **perfect
  refine yield of 0.9063** — the maximum achievable ore yield, *not* 100%: per material,
  `base_quantity × 0.9063 × mineral_unit_value`.
- **The leftover** below a full batch (`Q mod P`) is valued at the **ore's own market
  price** (you wouldn't refine a partial batch).
- The resolved rule's **basis** and the corp's **aggregate** price both the minerals and
  the leftover ore; the resolved **percentage** then applies to the whole line. Percentage
  is linear, so the engine blends the market values first (`unit_value = total / Q`) and
  rounds once (ADR-0020/0021).

**Ores are SDE category 25 (Asteroid)**, tagged onto `SdeType.category_id` at seed time
(joining `invGroups`). The seed also stores `SdeType.portion_size` and a new EVE-keyed
reference table `sde_type_materials` (`type_id, material_type_id, quantity` from
`invTypeMaterials`) — **only for ore types**, to keep it small. The minerals themselves
are ordinary seeded types, so they price through the same Fuzzwork path; the appraisal
unions the ore's mineral ids into its single price fetch.

## Consequences

- A new, composable pricing mode that reuses the rule hierarchy — no separate config
  surface, and per-ore granularity falls out for free. The rules table and editor gain a
  **Reprocess** column/checkbox; `RuleOut`/`RulePutRequest` gain `reprocess` (the API
  contract is otherwise unchanged). The editor only offers the checkbox for **ore
  targets** — a market group in (or under) one of the three ore branches (Standard / Moon
  / Ice Ores) or a type within one — since the flag is a no-op anywhere else.
- The seed now downloads two more Fuzzwork dumps (`invGroups`, `invTypeMaterials`) and
  writes `sde_type_materials`; re-run it after deploying this.
- A reprocess line whose minerals (and ore, for any leftover) are entirely unpriced is
  rejected "No market data", like any other unpriceable line. An unpriced *individual*
  mineral contributes 0 (minerals are liquid in the hubs, so this is rare).
- The line stores a single blended `unit_value`/`unit_price` **plus a per-line breakdown**
  (`AppraisalLine.reprocess`, JSON): the minerals yielded — name, yielded quantity, market
  unit value, value — and any sub-batch leftover. It's a snapshot (immutable like the rest
  of the line, ADR-0014), persisted at creation since recomputing later would use today's
  prices; the result view shows it under the ore line.

## Alternatives considered

- **Corp-wide `BuybackConfig` toggle** — simplest, but it *is* the "reprocess all ores or
  none" switch we wanted to avoid: no per-ore control, and a second config knob when the
  rule system already expresses "for this target, do X". Rejected in favor of the rule
  flag (one Ore-group rule still gives the corp-wide behavior).
- **100% refine yield** — simpler number, but wrong: no character refines at 100%. The
  0.9063 ore max is the honest "perfect" figure. A *configurable* per-corp yield
  (skills/structure) is future work; gas/scrap (different yields) are out of scope.
- **Floor partial batches to zero** (pure in-game mechanics) — pays nothing for sub-batch
  stacks, which is a bad buyback experience. Valuing the leftover at the ore's own price
  is fairer and was the chosen model.
