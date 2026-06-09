# 0006. Market data from Fuzzwork aggregates, cached with TTL

- **Status:** Accepted — extended by [ADR-0028](0028-esi-market-source-and-aggregation.md)
  (ESI as the source for hubs Fuzzwork doesn't cover)
- **Date:** 2026-06-04

## Context

Pricing needs aggregated **buy/sell** numbers per item at a market hub (e.g. Jita).
ESI exposes only *raw* market orders; for a hub like The Forge that is millions of
rows to fetch and aggregate ourselves, with rate limits and storage cost. The
product's "buy/sell/split" maps directly onto pre-computed aggregates.

## Decision

Source prices from the **Fuzzwork market aggregates API**:
`GET https://market.fuzzwork.co.uk/aggregates/?station=<hub>&types=<csv>`, which
returns per-`type_id` `buy`/`sell` objects (`weightedAverage`, `max`, `min`,
`median`, `percentile`, `volume`, `orderCount`). Requests are **batched** by type
id and **cached** in a `MarketPrice` table with `fetched_at` and a TTL (~1h).
Quotes read cache-first and fetch only misses/stale entries.

## Consequences

- Drastically less compute/storage than self-aggregating ESI orders; faster quotes.
- Introduces a third-party dependency: handle outages/timeouts gracefully (serve
  slightly-stale cache, mark items unpriced rather than failing the whole quote).
- The aggregate *field* used for buy/sell is configurable per corp
  (default `percentile` for manipulation resistance); `split = (buy + sell) / 2`.
- Hub is configurable (default Jita 4-4 station `60003760`, region fallback The
  Forge `10000002`).
- Send a descriptive `User-Agent` (per the `eve-esi` skill) and respect Fuzzwork's
  caching/rate expectations.

## Scope & hub coverage (MVP — confirmed 2026-06-07)

- **Default aggregate field is `percentile`** (manipulation resistance), confirmed;
  it stays per-corp configurable.
- **One hub, Jita 4-4, for MVP.** The `BuybackConfig.market_hub_id` column and the
  `(hub_id, type_id)` cache key already make the system multi-hub-capable, but the
  MVP surfaces only Jita; the config field is not yet exposed in the UI.
- **Fuzzwork's per-station aggregates cover only the five NPC trade hubs** (Jita
  `60003760`, Amarr `60008494`, Dodixie `60011866`, Rens `60004588`, Hek
  `60005686`), plus whole-region aggregates. Opening the other four hubs later is
  cheap (seed the station set + a picker) — see the "future" list in the
  architecture overview §13.
- **Private Upwell structure markets are out of scope.** Fuzzwork has no structure
  data; pricing at a player structure would require the authenticated
  `GET /markets/structures/{id}/` ESI endpoint (scope `esi-markets.structure_markets.v1`
  + per-structure access), our own aggregation, and a **stored EVE refresh token** —
  which [ADR-0004](0004-eve-sso-session-auth.md) deliberately avoids. A future
  feature that would supersede ADR-0004.

## Alternatives considered

- **Raw ESI market orders, aggregate in-house** — full control and no third party,
  but heavy and operationally costly; revisit only if Fuzzwork proves insufficient.
- **Adam4EVE / EVE Ref** — viable alternates; kept as fallbacks behind the market
  client interface so the source can be swapped without touching the pricing engine.
