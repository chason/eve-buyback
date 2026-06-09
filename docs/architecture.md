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
| 2 | **PostgreSQL** via SQLAlchemy 2.0 + Alembic (asyncpg) | [0024](adr/0024-postgresql-database.md) (supersedes [0002](adr/0002-sqlite-sqlalchemy-postgres-ready.md)) |
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
| 16 | **Per-request role resolution** from the DB (instant manager revoke) | [0016](adr/0016-per-request-role-resolution.md) |
| 17 | **CSRF**: `SameSite=Lax` + required custom header on mutations | [0017](adr/0017-csrf-custom-header.md) |
| 18 | **Layered backend**: interface / application / domain / data / plugins | [0018](adr/0018-layered-backend-architecture.md) |
| 19 | **Progressive docs** via layer-local `CLAUDE.md` | [0019](adr/0019-progressive-layer-documentation.md) |
| 20 | **`Decimal` not `float`** for money + quantity values | [0020](adr/0020-decimal-money-values.md) |
| 21 | **Appraisal computation/storage**: hybrid lines, half-even rounding, `Literal`+CHECK enums | [0021](adr/0021-appraisal-computation-and-storage.md) |
| 22 | **No sequential surrogate PKs in the API** (natural keys / random `public_id`) | [0022](adr/0022-no-sequential-pks-in-api.md) |
| 23 | **Frontend: Pico.css** + OpenAPI-generated TS types | [0023](adr/0023-frontend-styling-and-typegen.md) |
| 25 | **UUID PKs for app entities**; EVE ids demoted to unique `eve_id` columns, internal FKs via UUID | [0025](adr/0025-uuid-primary-keys.md) |
| 26 | **Ore reprocess pricing** as a per-rule flag (price ore by refined minerals, 0.9063 yield) | [0026](adr/0026-ore-reprocess-pricing.md) |
| 27 | **Deploy on Coolify** (Docker Compose app + managed PostgreSQL, Traefik TLS) | [0027](adr/0027-deploy-coolify.md) |
| 28 | **ESI market source** for non-Fuzzwork hubs (region orders + in-house aggregation) | [0028](adr/0028-esi-market-source-and-aggregation.md) |

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
| `Corporation` | `id` (UUID, PK), `eve_id` (EVE corp id, unique), `name`, `ceo_character_id`, `registered_at`, `registered_by` | The tenant. One row per registered corp. |
| `Character` | `id` (UUID, PK), `eve_id` (EVE char id, unique), `name`, `last_login_at` | Persisted only because managers must be referenceable. |
| `ManagerAssignment` | `id` (UUID, PK), `corporation_id`→corp, `character_id`→char (UUID FKs), `granted_by`, `granted_at` | Grants the Buyback Manager role. CEO is implicit (not stored here). |
| `BuybackConfig` | `id` (UUID, PK), `corporation_id`→corp (UUID FK, unique), `market_hub_id`, `default_basis`, `default_percentage`, `aggregate_field`, `default_accepted`, data-quality thresholds | Per-corp defaults = the "global" rule; `default_accepted=false` → whitelist-only buyback ([ADR-0007](adr/0007-pricing-rule-taxonomy.md)). |
| `PricingRule` | `id` (UUID, PK), `corporation_id`→corp (UUID FK), `target_kind` (`market_group`\|`type`), `target_id` (EVE id), `basis?`, `percentage`, `enabled`, `accepted`, `reprocess`, `compressed_only` | Overrides for a market group or a single type. `accepted=false` rejects matching items (blacklist, [ADR-0007](adr/0007-pricing-rule-taxonomy.md)); `reprocess`/`compressed_only` are ore flags ([ADR-0026](adr/0026-ore-reprocess-pricing.md)). |
| `MarketPrice` (cache) | `hub_id`, `type_id`, buy/sell aggregates, `volume`, `order_count`, `fetched_at` | Fuzzwork snapshot; EVE-keyed cache, TTL-expired. See [ADR-0006](adr/0006-market-data-fuzzwork.md). |
| `SdeType` (ref) | `type_id` (EVE id, PK), `name`, `group_id`, `category_id`, `market_group_id`, `volume`, `portion_size`, `published` | Seeded from SDE; EVE-keyed. `category_id` 25 = ore; `portion_size` = refine batch ([ADR-0026](adr/0026-ore-reprocess-pricing.md)). |
| `SdeTypeMaterial` (ref) | `type_id`, `material_type_id`, `quantity` (EVE-keyed) | Perfect-refine (100% base) yield per batch; seeded for ore types only ([ADR-0026](adr/0026-ore-reprocess-pricing.md)). |
| `SdeMarketGroup` (ref) | `market_group_id` (EVE id, PK), `parent_id`, `name` | Hierarchy for rule resolution; EVE-keyed. |
| `Appraisal` | `id` (UUID, PK), **`public_id`** (random slug), `corporation_id`→corp (UUID FK), `created_by`, `created_at`, `market_hub_id`, `accepted_total` | Persisted, immutable snapshot ([ADR-0014](adr/0014-persisted-appraisals.md)). |
| `AppraisalLine` | `id` (UUID, PK), `appraisal_id`→appraisal (UUID FK), `position`, `type_id`, `quantity`, `basis`, `percentage`, `unit_value`, `unit_price`, `line_total`, `status`, `reason?`, `reprocess?` (JSON) | Per-line snapshot; write-once, ordered by `position`. `reprocess` holds the mineral breakdown for a reprocess-priced ore ([ADR-0026](adr/0026-ore-reprocess-pricing.md)). |

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

The resolved rule also carries a **`reprocess`** flag ([ADR-0026](adr/0026-ore-reprocess-pricing.md)):
when set and the item is an **ore** (SDE category 25), the line is priced by its refined
minerals — whole refine batches at the 0.9063 perfect-ore yield, any sub-batch leftover
at the ore's own price — instead of the ore's market price. A single `reprocess` rule on
the *Ore* market group thus reprocess-prices every ore.

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
1. Browser → POST /api/v1/auth/login         → { url, state }   (PKCE + state)
2. Browser → EVE SSO authorize → user approves → redirect to SPA with ?code&state
3. Browser → POST /api/v1/auth/session {code, state}
4. Backend → exchange code (server-side secret) → verify → {character_id, name}
5. Backend → ESI: character→corp_id; corp→ceo_id        (public, no scope)
6. Backend → store **identity** in the cookie (character, corp, plus `is_ceo` /
   `is_director` from ESI at login). httpOnly Secure SameSite=Lax; no EVE token stored.
7. On **every** request the app **role is resolved from the DB** — `ceo` if the
   identity is the CEO, `manager` if a `ManagerAssignment` exists, else `member` — so a
   manager revoke takes effect on the next request
   ([ADR-0016](adr/0016-per-request-role-resolution.md)). CEO/Director status and corp
   membership re-derive from ESI at next login.
```

- **Unregistered corp:** members see "your corp isn't registered yet"; a logged-in
  **CEO or Director** may register their corp ([ADR-0015](adr/0015-corp-registration-ceo-or-director.md)).
  A non-CEO Director who registers is auto-granted Buyback Manager.
- **Scopes:** `publicData` + `esi-characters.read_corporation_roles.v1` — the roles
  scope is read once at login (Director check) and the token is not persisted
  ([ADR-0015](adr/0015-corp-registration-ceo-or-director.md)).
- **CSRF:** cookie auth + `SameSite=Lax` plus a required custom header on mutations
  ([ADR-0017](adr/0017-csrf-custom-header.md)).

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
| POST | `/auth/login` | public | Begin login: mint state + return SSO URL |
| POST | `/auth/session` | public | Complete login: exchange code → session |
| DELETE | `/auth/session` | member | Log out (clear session) |
| GET | `/auth/me` | member | Current character, corp, role |
| POST | `/corporations` | CEO | Register caller's corp |
| GET | `/corporations/me` | member | Registered corp (read) |
| GET | `/corporations/me/config` | member | Buyback config (read); a default "90% Jita Buy" is created at registration |
| PUT | `/corporations/me/config` | manager | Edit global defaults (data-quality thresholds: M7) |
| GET | `/corporations/me/rules` | member | List pricing rules |
| PUT/DELETE | `/corporations/me/rules/{target_kind}/{target_id}` | manager | Set (idempotent upsert) / remove the rule for a target — no surrogate id (ADR-0022) |
| GET | `/corporations/me/managers` | CEO | List managers |
| POST/DELETE | `/corporations/me/managers[/{character_id}]` | CEO | Grant/revoke manager |
| POST | `/appraisals` | member | Price a list of items → persist + return appraisal (the core endpoint) |
| GET | `/appraisals/{public_id}` | member | Fetch a saved appraisal (corp-scoped; doubles as share link) |
| GET | `/appraisals` | member | List appraisals (own; managers/CEO see the corp's) |
| GET | `/market-groups` / `/types/search` | member | Pickers for the rule editor |

`POST /appraisals` accepts `{ items: [{type_id, quantity}], paste? }` — structured
items and/or a raw EVE inventory paste that the backend parses and name-resolves via
the SDE ([ADR-0021](adr/0021-appraisal-computation-and-storage.md)). It stores an
immutable snapshot and returns a `public_id` for later reference
([ADR-0014](adr/0014-persisted-appraisals.md)). Lines with no usable market price (or
an unresolved pasted name) are rejected with a reason; configurable data-quality
thresholds are M7. The combined item count (structured + parsed paste) is capped at
**1000** — one EVE contract's worth (`422` over the limit).

## 11. Repository layout

The backend follows a strict **layered architecture** with dependencies pointing
inward only ([ADR-0018](adr/0018-layered-backend-architecture.md)); see `CLAUDE.md`
(root + per-layer) for the rules.

```
buyback/
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py          # app factory + lifespan; wires middleware, routers, error handlers
│   │   ├── config.py        # pydantic-settings (env)
│   │   ├── interface/       # API layer: v1/ routers + deps, security, middleware, error mapping
│   │   ├── application/     # use cases (auth, corporations; pricing + appraisals later)
│   │   ├── domain/          # pure functions (roles, auth; rule-resolution later)
│   │   ├── data/            # db engine, models/, records.py, repositories/ (+ SDE/price models later)
│   │   ├── plugins/         # outside-API gateways: EVE ESI, SSO (+ Fuzzwork market later)
│   │   └── schemas/         # API request/response DTOs
│   ├── alembic/            # migrations
│   └── tests/
├── frontend/
│   ├── package.json
│   ├── vite.config.ts      # dev proxy → backend; Vitest config
│   ├── eslint.config.js
│   ├── openapi.json        # exported backend schema → src/api/schema.d.ts (gen:api)
│   └── src/                # api/ (incl. generated schema.d.ts), components/, pages/, lib/, test/
└── docs/{architecture.md, adr/}
```

Upcoming M4/M5 work slots in **by layer** rather than as new top-level packages: the
Fuzzwork client is a `plugins/` gateway; SDE and price-cache models + repositories go
in `data/`; the pricing/appraisal use cases go in `application/`; rule-resolution
helpers go in `domain/`; routers stay thin in `interface/v1/`. The market-refresh
scheduler ([ADR-0010](adr/0010-in-process-scheduling.md)) wires in at the app boundary
in `main.py`.

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
6. **Frontend** (shipped core-first) — **6a**: OpenAPI type-gen, app shell, the
   appraisal tool + shareable result view (generated API types, Pico.css). **6b**:
   corp config view, manager rule editor, appraisal history (role-gated nav).
7. **Data quality + polish**
   - Configurable **rejection thresholds** (min order count, max price age) fed into
     the existing line-rejection path.
   - **SDE-readiness signal.** When the SDE tables are empty, every appraisal silently
     rejects every line as "Unknown item" with no hint the reference data is unseeded
     (an operator running fresh — or after a DB reset — hits this). Surface it: a
     readiness check (e.g. `/health` reports `sde: seeded|empty` from `sde_metadata`)
     and an empty-state banner on the appraisal page prompting `python -m app.sde.seed`.
   - **Packaging/Docker** for self-hosting — *done*: a root multi-stage `Dockerfile`
     builds the SPA and serves it from the backend under `/` (history fallback to
     `index.html`) alongside `/api/v1`; the entrypoint runs `alembic upgrade head`
     on boot; `docker-compose.yml` bundles Postgres + the app. The SDE seed
     (`python -m app.sde.seed`) is a one-time post-deploy step ([ADR-0012](adr/0012-single-deployable-packaging.md)).
     Production hosting targets **Coolify** (Docker Compose app + managed PostgreSQL
     behind Traefik) via `docker-compose.coolify.yml` — runbook in
     [`deploy-coolify.md`](deploy-coolify.md), decision in [ADR-0027](adr/0027-deploy-coolify.md).

## 13. Out of scope (MVP) / future

- Acting on members' behalf via ESI (contracts/wallet) → would need stored,
  encrypted refresh tokens.
- Multi-hub pricing beyond Jita — opening the other four NPC hubs (Amarr / Dodixie /
  Rens / Hek) is cheap later; the data layer is already hub-keyed ([ADR-0006](adr/0006-market-data-fuzzwork.md)).
- **Per-structure (private Upwell) pricing** — needs authenticated structure-market
  ESI + a stored refresh token, superseding [ADR-0004](adr/0004-eve-sso-session-auth.md);
  Fuzzwork has no structure data ([ADR-0006](adr/0006-market-data-fuzzwork.md)).
- **Configurable contract recipient.** The appraisal page tells the member to make
  the buyback contract out to **their corporation** (the implicit recipient today).
  A future option would let a manager set the recipient per corp — a specific
  character (a buyer alt), a holding/other corp, or an alliance — stored on
  `BuybackConfig` and shown in the appraisal's contract instructions.
- Payout automation / contract verification.
- Public hosted multi-corp SaaS hardening (billing, abuse controls) — the data
  model supports it, but ops are out of MVP.
- Audit log / analytics dashboards.

## 14. Resolved questions

All MVP open questions are settled (decisions of 2026-06-07):

- ~~Default Fuzzwork aggregate for "buy"/"sell"?~~ **`percentile`** (manipulation
  resistance), per-corp configurable ([ADR-0006](adr/0006-market-data-fuzzwork.md)).
- ~~Hub limited to Jita, or selectable among the major hubs?~~ **Jita only for MVP.**
  Fuzzwork's per-station aggregates cover just the five NPC hubs anyway; the data
  layer is already hub-keyed, so the other four are a cheap future add, and private
  Upwell structures are a token-requiring future feature (§13;
  [ADR-0006](adr/0006-market-data-fuzzwork.md)).
- ~~Should a non-CEO Director be able to register the corp?~~ **CEO or Director**
  ([ADR-0015](adr/0015-corp-registration-ceo-or-director.md)).
- ~~Persist every submit, or add an ephemeral "preview" mode?~~ **Always persist on
  submit** ([ADR-0014](adr/0014-persisted-appraisals.md)).
- ~~Appraisal retention?~~ **Keep indefinitely** for MVP — rows are small; add
  pruning/archival only if it ever grows.
```
