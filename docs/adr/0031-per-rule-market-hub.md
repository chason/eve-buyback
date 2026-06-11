# 0031. Per-rule market-hub override

- **Status:** Accepted
- **Date:** 2026-06-10
- **Relates to:** [ADR-0007](0007-pricing-rule-taxonomy.md) (rule taxonomy),
  [ADR-0028](0028-esi-market-source-and-aggregation.md) / [ADR-0029](0029-encrypted-refresh-token-structures.md)
  (hub kinds, string ids), [ADR-0014](0014-persisted-appraisals.md) (snapshots)

## Context

A corp prices everything at one hub (`BuybackConfig.market_hub_id`), but real buyback
programs price different item classes at different markets — e.g. ore at the corp's
own Upwell structure (where it's consumed) while everything else prices at Jita. The
rule taxonomy (ADR-0007) already targets types and market groups; the missing piece is
letting a rule say **where** its matches price, not just how.

## Decision

A pricing rule may carry an optional **market-hub override**; matched items price
there instead of the corp default.

- **Storage mirrors the config hub:** `pricing_rules` gains the nullable quartet
  `market_hub_id` (string EVE id, ADR-0029), `market_hub_kind`, `market_region_id`,
  `market_hub_name` — the region and display name are resolved and cached at save
  time by the same `_resolve_hub` validation the config uses, so appraisal-time
  descriptor construction needs no lookups. All-null means "inherit the corp default"
  (parallel to `basis = null`). Any hub kind the config supports is allowed,
  including structures (the corp's one token covers all structure hubs).
- **Resolution carries the hub:** `RuleSpec`/`ResolvedRule` gain `hub:
  HubDescriptor | None`; `resolve_rule` passes the winning rule's hub through and the
  default branch yields `None`. The corp default never enters the domain — the
  application substitutes it.
- **Appraisals fetch per hub:** `create_appraisal` groups the priced type ids by
  their resolved hub and calls the read-through market cache once per distinct hub
  (the cache is already keyed `(hub_id, type_id)`). Reprocess minerals join **the
  ore's** hub bucket. An override equal to the config hub is normalized away (one
  fetch, no annotation). A failing hub degrades only its own lines ("No market
  data") — the rest of the appraisal prices normally.
- **Lines snapshot their hub:** `appraisal_lines` gains nullable
  `market_hub_id`/`market_hub_name`, set **only** when a rule priced the line away
  from the appraisal's default hub (null = the header's hub). The name is frozen
  because structures aren't in the SDE (same precedent as the drop-off snapshot,
  ADR-0030). The result UI annotates overridden lines with "@ hub".
- **UI:** the hub picker is extracted into a shared `HubPicker` component (Config +
  the rule editor); the rule editor adds a "(corp default hub)" leading choice and
  the rules table shows each rule's hub.

## Consequences

- An appraisal can now hit several market sources in one request; latency grows with
  the number of *distinct* hubs, not items, and the per-hub cache keeps repeat
  appraisals cheap.
- Like the config hub, a rule's cached region/name can drift from the SDE; accepted
  (same trade-off as ADR-0028).
- A structure-hub rule depends on the corp's structure authorization (ADR-0029);
  while it's broken those lines degrade to cache rather than failing the appraisal.
- Older appraisal lines have null hub columns and render as default-hub lines.

## Alternatives considered

- **A normalized `market_hubs` table** (rules/config FK-ing a shared hub row instead
  of carrying the quartet) — rejected because a hub here is **not an entity we own**;
  it's a *reference* to an external EVE station/structure plus resolution results
  **cached at save time** (kind/region/name are memoization, not state — their
  canonical source is the SDE/ESI). Normalization wouldn't buy its usual benefits:
  - *Update anomalies don't apply.* The cached name/region refresh whenever a rule or
    config is saved, and SDE drift is explicitly accepted (ADR-0028); a shared row
    wouldn't eliminate staleness, only centralize it.
  - *The expensive shared data is already normalized.* `market_prices` is keyed
    `(hub_id, type_id)`, so every rule/config pointing at the same hub shares one
    price cache today; the duplicated quartet itself is tens of bytes per rule.
  - *Appraisal lines must stay denormalized regardless* — they're immutable snapshots
    (ADR-0014), so a rename of a shared hub row must never rewrite history. The table
    would only cover rules + config, where duplication is cheapest.
  - *It raises tenancy questions for free.* Structure names come from a corp's own
    authorized ESI search (ADR-0029): a global table makes one corp's resolution (or
    mistake) another corp's display name; a per-corp table is the same quartet again,
    one join away — and rules are read on every appraisal.
  - It also keeps the rule consistent with `BuybackConfig`, which has carried this
    exact quartet since ADR-0028, so both flow through the same `_resolve_hub`
    validation. **Revisit** if hubs grow their own lifecycle — per-hub settings
    (data-quality thresholds, refresh cadence), a "corp's saved hubs" picker, or
    enough rules that bulk-renaming a hub matters; at that point a hub becomes an
    owned entity and the FK design earns its keep.
- **Multiple hubs on the config with per-rule selection by name** — indirection
  without benefit at this scale; rejected.
- **Hub only on market-group rules** — arbitrary asymmetry; type rules carry it too.
- **Snapshot the hub on every line** — redundant for the common single-hub case;
  null-means-default keeps old rows valid and the data small.
