# 0008. Configurable data-quality rejection

- **Status:** Accepted
- **Date:** 2026-06-04

## Context

Buyback at "X% of market" is only fair when the market data is sound. Thinly-traded
items, stale snapshots, or one-sided/empty order books produce misleading prices a
corp may not want to honor. The product requires the system to **optionally reject
items with poor data** in the chosen hub.

## Decision

Add per-corp, configurable **data-quality thresholds** to `BuybackConfig`, applied
during quoting. An item line is **rejected** (priced at 0 / excluded from the
accepted total, with a reason) when its Fuzzwork aggregate fails any enabled check:

- **Insufficient liquidity** — `volume` or `orderCount` for the relevant side below
  a minimum.
- **Stale data** — `fetched_at` older than a max age (and a refresh attempt failed).
- **No price on the needed side** — e.g. a "buy" basis with no buy orders.
- *(optional)* **Spread sanity** — implausible buy/sell spread.

Each check is toggleable with a numeric threshold; defaults are conservative.
Rejections are returned per line so the UI can explain *why*.

## Consequences

- Corps avoid overpaying on manipulated/thin items; members get clear feedback.
- Thresholds live with the corp config and are editable by managers
  ([ADR-0005](0005-authorization-roles.md)).
- Pairs with the manipulation-resistant default aggregate (`percentile`,
  [ADR-0006](0006-market-data-fuzzwork.md)).
- A "warn but still price" mode can be added later; MVP rejects.

## Alternatives considered

- **No rejection (price everything)** — simplest, but exposes corps to bad fills;
  contradicts the requirement.
- **Hard-coded thresholds** — easier, but corps differ in risk appetite and item
  mix; configurability is cheap to add now.
