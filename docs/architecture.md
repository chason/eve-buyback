# Buyback — Architecture & MVP Plan

## 1. Purpose & scope

A self-hostable web app that lets an EVE Online corporation run a **buyback
program**: members paste/select items, the system quotes a price derived from a
market hub (e.g. "90% Jita Buy"), and corp **Buyback Managers** configure the
pricing rules. It runs standalone — no spreadsheets or third-party tooling
required by the corp.

**MVP boundary:** authentication, corp registration, role-based access, a pricing
rule editor, and a quote/appraisal endpoint backed by Fuzzwork market data. The
only API consumer is our own TypeScript SPA, but the API is designed so other
consumers can be added later (see [ADR-0011](adr/0011-api-contract-and-typescript-types.md)).

## 2. Decision summary

| # | Decision | ADR |
|---|----------|-----|
| 1 | REST backend on **FastAPI + Pydantic** | [0001](adr/0001-fastapi-backend.md) |
| 2 | **SQLite via SQLAlchemy 2.0 + Alembic**, Postgres-ready | [0002](adr/0002-sqlite-sqlalchemy-postgres-ready.md) |
| 3 | **Multi-tenant** via `corp_id` row scoping | [0003](adr/0003-multi-tenant-corp-scoping.md) |
| 4 | **EVE SSO** login, backend-issued session cookie, no persisted EVE tokens | [0004](adr/0004-eve-sso-session-auth.md) |
| 5 | **Roles**: member / Buyback Manager / CEO (CEO auto-derived from ESI) | [0005](adr/0005-authorization-roles.md) |
| 6 | Market prices from **Fuzzwork aggregates**, cached with TTL | [0006](adr/0006-market-data-fuzzwork.md) |
| 7 | Pricing rules on **EVE market groups** + type overrides + global default | [0007](adr/0007-pricing-rule-taxonomy.md) |
| 8 | Configurable **data-quality rejection** of poorly-priced items | [0008](adr/0008-data-quality-rejection.md) |
| 9 | **Seed a subset of the EVE SDE** (types, market groups) into the DB | [0009](adr/0009-sde-reference-data.md) |
| 10 | **No external broker**; in-process scheduler for market refresh | [0010](adr/0010-in-process-scheduling.md) |
| 11 | **Versioned API + TS types generated from OpenAPI** | [0011](adr/0011-api-contract-and-typescript-types.md) |
| 12 | **Single deployable** (backend serves the built SPA), config via env | [0012](adr/0012-single-deployable-packaging.md) |
| 13 | Frontend: **React + Vite + TanStack Query** | [0013](adr/0013-frontend-stack.md) |
| 14 | **Persisted, immutable appraisals** with shareable ids | [0014](adr/0014-persisted-appraisals.md) |
| 15 | Corp registration by **CEO or Director** (adds roles scope) | [0015](adr/0015-corp-registration-ceo-or-director.md) |

## 3. System context

```
┌────────────┐   HTTPS / JSON    ┌──────────────────────────┐
│  Browser   │ ────────────────▶ │   Buyback backend        │
│  (React    │ ◀──── cookie ──── │   (FastAPI, Python)       │
│   SPA)     │                   │                          │
└────────────┘                   │  ┌────────────────────┐  │
      │ redirect to login        │  │ SQLite (app data + │  │
      ▼                          │  │ SDE + price cache) │  │
┌────────────┐                   │  └────────────────────┘  │
│  EVE SSO   │ ◀── code exchange ─┤                          │
│ login.eve… │                   │   ├─▶ Fuzzwork aggregates │
└────────────┘                   │   └─▶ ESI (identity/corp) │
                                 └──────────────────────────┘
```

- **Browser ↔ backend:** all EVE data flows through our API; the browser never
  calls ESI/Fuzzwork directly (secrets, CORS, caching — see the `eve-esi` skill).
- **EVE SSO:** identity only. **ESI:** public character→corp and corp→CEO lookups.
  **Fuzzwork:** aggregated market prices.

## 4. Domain model (MVP entities)

| Entity | Key fields | Notes |
|--------|-----------|-------|
| `Corporation` | `corp_id` (EVE id, PK), `name`, `ceo_character_id`, `registered_at`, `registered_by` | The tenant. One row per registered corp. |
| `Character` | `character_id` (PK), `name`, `last_login_at` | Persisted only because managers must be referenceable. |
| `ManagerAssignment` | `corp_id`, `character_id`, `granted_by`, `granted_at` | Grants the Buyback Manager role. CEO is implicit (not stored here). |
| `BuybackConfig` | `corp_id` (PK), `market_hub_id`, `default_basis`, `default_percentage`, `aggregate_field`, data-quality thresholds | Per-corp defaults = the "global" rule. |
| `PricingRule` | `id`, `corp_id`, `target_kind` (`market_group`\|`type`), `target_id`, `basis?`, `percentage`, `enabled` | Overrides for a market group or a single type. |
| `MarketPrice` (cache) | `hub_id`, `type_id`, buy/sell aggregates, `volume`, `order_count`, `fetched_at` | Fuzzwork snapshot; TTL-expired. See [ADR-0006](adr/0006-market-data-fuzzwork.md). |
| `SdeType` (ref) | `type_id`, `name`, `group_id`, `market_group_id`, `volume`, `published` | Seeded from SDE. |
| `SdeMarketGroup` (ref) | `market_group_id`, `parent_id`, `name` | Hierarchy for rule resolution. |
| `Appraisal` | `id`, **`public_id`** (random slug), `corp_id`, `created_by`, `created_at`, `market_hub_id`, `accepted_total` | Persisted, immutable snapshot ([ADR-0014](adr/0014-persisted-appraisals.md)). |
| `AppraisalLine` | `appraisal_id`, `type_id`, `quantity`, `basis`, `percentage`, `unit_value`, `unit_price`, `line_total`, `status`, `reason?` | Per-line snapshot; write-once. May be JSON on the parent for MVP. |

`basis ∈ {buy, sell, split}`. `market_hub_id` defaults to **Jita 4-4** (station
`60003760`); region fallback **The Forge** (`10000002`).

## 5. Pricing rule resolution

For a given `type_id`, the most-specific rule wins
([ADR-0007](adr/0007-pricing-rule-taxonomy.md)):

1. **Type override** — a `PricingRule` with `target_kind=type, target_id=type_id`.
2. **Market group** — walk the type's `market_group_id` up the `SdeMarketGroup`
   parent chain; the **nearest ancestor** that has an enabled `market_group` rule wins.
3. **Global** — fall back to `BuybackConfig` defaults.

This makes "Ore → Moon Ores → specific ore" fall out naturally: a rule on the
*Moon Ores* market group covers all moon ores, a broader rule on *Ore* covers the
rest, and a `type` rule pins one item.

## 6. Quote / appraisal computation

```
parse items (name→type_id via SdeType, or accept type_ids + quantities)
  └─ for each line:
       rule        = resolve_rule(corp, type_id)          # §5
       agg         = market_price(corp.hub, type_id)       # cache or Fuzzwork
       if poor_data(agg, corp.thresholds):  reject(reason) # ADR-0008
       unit        = pick(agg, rule.basis, corp.aggregate_field)
                     # buy→buy.*, sell→sell.*, split→(buy+sell)/2
       unit_price  = unit * rule.percentage / 100
       line_total  = unit_price * quantity
  └─ accepted_total = Σ accepted line_totals
persist Appraisal + AppraisalLines (write-once snapshot, random public_id)
return { public_id, created_at, market_hub,
         lines:[{type, qty, basis, pct, unit_value, unit_price, total, status, reason?}],
         accepted_total, rejected:[…] }
```

`aggregate_field` (e.g. `weightedAverage`, `max`, `percentile`) is configurable;
default to **percentile** for manipulation resistance, matching common appraisal
tools.

The result is **persisted as an immutable appraisal** and returned with a stable
`public_id` so it can be referenced/shared later; reads never recompute
([ADR-0014](adr/0014-persisted-appraisals.md)).

## 7. Authentication & authorization flow

EVE SSO authorization-code flow; the backend is the confidential client and issues
its own session ([ADR-0004](adr/0004-eve-sso-session-auth.md),
[ADR-0005](adr/0005-authorization-roles.md)):

```
1. Browser → GET /api/v1/auth/login-url     → { url, state }   (PKCE + state)
2. Browser → EVE SSO authorize → user approves → redirect to SPA with ?code&state
3. Browser → POST /api/v1/auth/login {code, state}
4. Backend → exchange code (server-side secret) → verify → {character_id, name}
5. Backend → ESI: character→corp_id; corp→ceo_id        (public, no scope)
6. Backend → resolve role:
     ceo      if character_id == corp.ceo_id
     manager  if ManagerAssignment(corp_id, character_id) exists
     member   otherwise
   set httpOnly Secure SameSite=Lax session cookie; no EVE token stored
7. Subsequent requests authorized from the session; corp membership re-derived
   from ESI on each login so corp changes are picked up.
```

- **Unregistered corp:** members see "your corp isn't registered yet"; a logged-in
  **CEO or Director** may register their corp ([ADR-0015](adr/0015-corp-registration-ceo-or-director.md)).
  A non-CEO Director who registers is auto-granted Buyback Manager.
- **Scopes:** `publicData` + `esi-characters.read_corporation_roles.v1` — the roles
  scope is read once at login (Director check) and the token is not persisted
  ([ADR-0015](adr/0015-corp-registration-ceo-or-director.md)).
- **CSRF:** cookie auth + `SameSite=Lax` plus a required custom header / CSRF token
  on mutations.

## 8. Market data & caching

- Source: **Fuzzwork aggregates** — `GET https://market.fuzzwork.co.uk/aggregates/?station=<hub>&types=<csv>`
  returns per-`type_id` `buy`/`sell` objects (`weightedAverage`, `max`, `min`,
  `median`, `percentile`, `volume`, `orderCount`).
- Batch requests by type id; **cache** rows in `MarketPrice` with `fetched_at`,
  TTL ~1h. Quotes read cache first, fetch misses/stale on demand.
- Optional periodic refresh of "hot" types via the in-process scheduler
  ([ADR-0010](adr/0010-in-process-scheduling.md)) — no Redis/Celery.

## 9. SDE reference data

Rule resolution and name↔id lookups need the type taxonomy locally
([ADR-0009](adr/0009-sde-reference-data.md)):

- Seed `SdeType` and `SdeMarketGroup` from Fuzzwork's SDE conversions at deploy
  time via `backend/app/sde/seed.py`.
- Re-run the seed when CCP ships a new SDE (each expansion). Version-stamp the import.

## 10. API surface (MVP)

All under `/api/v1`. Auth via session cookie; manager/CEO gating noted.

| Method | Path | Role | Purpose |
|--------|------|------|---------|
| GET | `/auth/login-url` | public | Build SSO URL + state |
| POST | `/auth/login` | public | Exchange code → session |
| POST | `/auth/logout` | member | Clear session |
| GET | `/auth/me` | member | Current character, corp, role |
| POST | `/corporations` | CEO | Register caller's corp |
| GET | `/corporations/me` | member | Corp + buyback config (read) |
| PUT | `/corporations/me/config` | manager | Edit global defaults / thresholds |
| GET | `/corporations/me/rules` | member | List pricing rules |
| POST/PATCH/DELETE | `/corporations/me/rules[/{id}]` | manager | CRUD pricing rules |
| GET | `/corporations/me/managers` | CEO | List managers |
| POST/DELETE | `/corporations/me/managers[/{character_id}]` | CEO | Grant/revoke manager |
| POST | `/appraisals` | member | Price a list of items → persist + return appraisal (the core endpoint) |
| GET | `/appraisals/{public_id}` | member | Fetch a saved appraisal (corp-scoped; doubles as share link) |
| GET | `/appraisals` | member | List appraisals (own; managers/CEO see the corp's) |
| GET | `/market-groups` / `/types/search` | member | Pickers for the rule editor |

`POST /appraisals` accepts `{ items: [{type_id, quantity}] }` and (optionally) a raw
EVE inventory paste string the backend parses to type ids. It stores an immutable
snapshot and returns a `public_id` for later reference ([ADR-0014](adr/0014-persisted-appraisals.md)).

## 11. Repository layout

```
buyback/
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py          # app + lifespan + static SPA serving
│   │   ├── config.py        # pydantic-settings (env)
│   │   ├── db.py            # async engine/session
│   │   ├── models/         # SQLAlchemy 2.0 models
│   │   ├── schemas/        # Pydantic request/response DTOs
│   │   ├── api/v1/         # routers: auth, corporations, rules, managers, quote
│   │   ├── auth/           # SSO client, session, FastAPI deps (require_role)
│   │   ├── pricing/        # rule resolution + quote engine
│   │   ├── market/         # Fuzzwork client + cache
│   │   ├── sde/            # seed + reference access
│   │   └── scheduler.py    # APScheduler refresh job
│   ├── alembic/            # migrations
│   └── tests/
├── frontend/
│   ├── package.json
│   ├── vite.config.ts      # dev proxy → backend
│   └── src/{api,auth,pages,components}/
└── docs/{architecture.md, adr/}
```

Reuse the `eve-esi` project skill's patterns for the ESI client, SSO exchange,
Fuzzwork usage, image/link helpers, and caching.

## 12. MVP milestones

1. **Scaffold** — FastAPI app + SQLAlchemy/Alembic + Vite/React; health check; CI.
2. **Auth** — SSO login-url/login/logout/me; session cookie; `/auth/me` in the SPA.
3. **Tenancy & roles** — corp registration, CEO auto-detect, manager grant/revoke,
   `require_role` deps.
4. **SDE seed + market client** — seed types/market groups; Fuzzwork client + cache.
5. **Pricing & appraisals** — config + rule CRUD + resolution engine; persist
   appraisals (`POST /appraisals`, `GET /appraisals/{public_id}`, list).
6. **Frontend** — login, corp buyback view, appraisal tool (with shareable result
   links), appraisal history, manager rule editor (generated API types).
7. **Data quality + polish** — rejection thresholds, packaging/Docker.

## 13. Out of scope (MVP) / future

- Acting on members' behalf via ESI (contracts/wallet) → would need stored,
  encrypted refresh tokens.
- Multi-hub pricing per corp beyond a single configured hub.
- Payout automation / contract verification.
- Public hosted multi-corp SaaS hardening (billing, abuse controls) — the data
  model supports it, but ops are out of MVP.
- Audit log / analytics dashboards.

## 14. Open questions

- Which Fuzzwork aggregate is the default "buy"/"sell" number — `percentile`,
  `weightedAverage`, or `max`/`min`? (Defaulting to `percentile`; confirm.)
- Should the corp's hub be limited to Jita for MVP, or selectable among the major
  hubs (Amarr/Dodixie/Rens/Hek)?
- ~~Should a non-CEO Director be able to register the corp?~~ Resolved: CEO **or**
  Director ([ADR-0015](adr/0015-corp-registration-ceo-or-director.md)).
- Should every submit persist an appraisal, or add an ephemeral "preview" mode to
  avoid clutter while typing? (MVP persists on submit — [ADR-0014](adr/0014-persisted-appraisals.md).)
- Appraisal retention: keep indefinitely (MVP) or add pruning/archival later?
```
