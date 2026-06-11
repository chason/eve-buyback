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

- **Multiple hubs on the config with per-rule selection by name** — indirection
  without benefit at this scale; rejected.
- **Hub only on market-group rules** — arbitrary asymmetry; type rules carry it too.
- **Snapshot the hub on every line** — redundant for the common single-hub case;
  null-means-default keeps old rows valid and the data small.
