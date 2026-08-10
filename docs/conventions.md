# Coding conventions

The rulebook for writing code in this repo — for humans and LLMs alike. Distilled
from `CLAUDE.md` (root + layer-local) and the ADRs in [`adr/`](adr/README.md).
Each rule cites its source ADR; this doc says **what** to do, the ADR records
**why**. If this doc and an ADR disagree, the ADR wins — then fix this doc.

Rules from ADRs still marked *Proposed* are tagged *(proposed)*.

**Maintenance:** when an ADR lands or a convention changes, update this file in the
same change (like `CLAUDE.md`'s Layout/Commands sections).

## The ten rules most worth internalizing

1. **Dependencies point inward only**: `interface → application → domain`, with
   `application` also using `data` and `plugins`. Never import the other way. (ADR-0018)
2. **`Decimal`, never `float`**, for all money and quantity values. (ADR-0020)
3. **Repositories return Pydantic records, never ORM entities — and never
   `commit()`**; the application layer owns the unit of work. (ADR-0018)
4. **Raise typed application errors, never `HTTPException`** outside `interface/`;
   the mapping to status codes lives in `interface/errors.py`. (ADR-0018)
5. **Every tenant-owned query filters by the session's corp.** Cross-corp access
   is a bug and gets a test. (ADR-0003)
6. **No sequential surrogate PKs in the API.** Natural EVE ids are fine; synthetic
   handles are random `public_id`s; UUIDs never surface. (ADR-0022, ADR-0025)
7. **Regenerate TS types after any backend DTO change**; never hand-edit
   `frontend/src/api/schema.d.ts`. (ADR-0011, ADR-0023)
8. **Market/ESI failures degrade, never fail the whole request** — stale cache or
   per-line "No market data", not a 500. (ADR-0006, ADR-0028, ADR-0031)
9. **Any change to how an EVE token is used updates the Privacy page + its test in
   the same change.** (`CLAUDE.md`; exercised by ADR-0038, 0042, 0044, 0045, 0048)
10. **Never bump `APP_VERSION` in a PR** — CI assigns it on merge. (ADR-0032)

## Backend layering (ADR-0018, ADR-0019)

```
interface  →  application  →  domain
                  ↓
                data        plugins
```

- **`interface/`** — routers in `v1/` hold only API concerns (status codes,
  request/response wiring, session cookie) and call the application layer. No
  business logic, no SQL. Map application results/records to the DTOs in
  `schemas/` here.
- **`application/`** — one use-case function per user action. Orchestrates
  `plugins/`, `domain/`, and `data/` repositories; owns `session.commit()`;
  raises typed errors from `errors.py` (register each new error's status in
  `interface/errors.py._STATUS`, unmapped → 400). Returns `data/` records or
  application models — never `schemas/` DTOs, never ORM. No HTTP, no SQL.
  One documented exception to the single unit of work: `market.persist_market_rows`
  commits the shared price cache in its own UoW (idempotent upsert — see its docstring).
- **`domain/`** — small, single-purpose **pure functions**, no I/O. Pricing
  resolution, aggregation, role ordering, id generation live here.
- **`data/`** — the only layer that talks to the database. ORM entities in
  `models/`, query/write functions in `repositories/` (separate files — no queries
  in model files). Repositories take the `AsyncSession` as their first argument
  (injected at the interface boundary), may `flush()`/`refresh()` but never
  `commit()`, and return the Pydantic read-models in `records.py`. May use
  `domain/` types; must not import `application/`, `interface/`, `plugins/`, or
  `schemas/`.
- **`plugins/`** — gateways to outside APIs (ESI, SSO, Fuzzwork, cache). Pure
  transport; return Pydantic models.
- **New feature pattern:** use case in `application/`, pure logic pushed down into
  `domain/`, persistence in a `data/` repository returning a record, thin router
  in `interface/v1/`, outside-API access via a `plugins/` gateway.
- Layer-local rules live in that layer's own `CLAUDE.md` (currently `app/data/`
  and `app/application/`) — read them before changing files there, keep them
  current, and document invariants + the "why", not file inventories. (ADR-0019)

## Data & persistence

- **PostgreSQL is the sole database** — dev, test, and prod. Never add SQLite
  support, dialect branching, or Alembic `render_as_batch`. Upserts use the
  `postgresql` dialect's `ON CONFLICT`. (ADR-0024)
- **UUID primary keys for app-owned entities** (ADR-0025):
  - `id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)`.
  - The entity's EVE id goes in a unique `eve_id` column — never as the PK.
  - Internal FK columns keep the `<entity>_id` name but hold the referenced row's
    **UUID** (`ForeignKey("corporations.id")`), never the EVE id.
  - Repositories look up by `eve_id` and store/join UUIDs; use cases resolve
    EVE id → UUID once at the boundary and thread the UUID from there.
  - Records that back an EVE-id DTO field expose it via Pydantic
    `validation_alias="eve_id"` — the API contract speaks EVE ids only.
  - Reference/cache tables stay EVE-keyed by design (`SdeType`, `SdeMarketGroup`,
    `MarketPrice`, `SdeMetadata`; `PricingRule.target_id` is an EVE id). Do not
    UUID-key these. Denormalized audit-actor fields (`*_character_id`) stay EVE
    ints with no FK.
- **`BigInteger` for EVE-scale integers** (EVE ids exceed 2³¹; plain
  `Mapped[int]` is 32-bit on Postgres) with DTO bounds. (ADR-0024, ADR-0036)
- **`DateTime(timezone=True)`; datetimes are tz-aware end-to-end** — no
  naive-datetime guards. (ADR-0024)
- **Closed-set (enum-like) fields**: a `Literal` in `domain/` is the single source
  of truth — it types domain functions, validates DTOs (Pydantic → 422), and
  builds the column via the `check_enum(...)` helper (VARCHAR + CHECK). Never
  native DB enums, Python `Enum` classes, or bare `str`. (ADR-0021)
- **Adding a model:** define it in `models/<entity>.py` (import `Base` from
  `data.db`), register it in `models/__init__.py`, then
  `uv run alembic revision --autogenerate`.
- **Migrations run automatically on every boot** (`alembic upgrade head` in the
  entrypoint) — keep them safe and idempotent. (ADR-0012, ADR-0027)
- **Multi-tenancy is `corp` row scoping**: every tenant-owned table carries the
  corp FK and every query filters by the session's corp, via shared helpers/deps —
  no route applies (or forgets) its own scoping. SDE/reference tables are shared,
  not tenant data. (ADR-0003, ADR-0009)

## Money, quantities, and ids on the wire

- **`Decimal` end-to-end**: SQLAlchemy `Numeric` (unconstrained — no fixed
  precision/scale), Pydantic `Decimal`. Order counts stay `int`. (ADR-0020)
- **Parse to `Decimal` at the ingest boundary from text** — wire/CSV strings, or
  `json.loads(..., parse_float=Decimal)` for ESI. Never round-trip through
  `float`. (ADR-0020, ADR-0028, ADR-0037)
- **Rounding is a computation-time domain decision, not storage**: cache and
  reference tables keep full precision; payout money rounds with banker's
  rounding to 2 dp — `Decimal.quantize(Decimal("0.01"), ROUND_HALF_EVEN)` — and
  `accepted_total` sums the rounded line totals. (ADR-0020, ADR-0021)
- **Hub and location ids are strings** everywhere (DB, API, TS) — 64-bit
  structure ids exceed int32 and JS's safe-integer range. NPC station / region /
  type ids stay integers. (ADR-0029, ADR-0030)
- **Never expose sequential surrogate PKs** in DTOs or URLs (ADR-0022):
  - Natural EVE ids (`character_id`, `type_id`, …) are fine — the rule is about
    synthetic keys.
  - Key externally-addressable resources by a natural id where one exists (rules:
    `target_kind` + `target_id`); mint a random `public_id` (`domain/ids.py`)
    only when there's no natural key or the handle is a share link (appraisals).
  - Singleton-per-target writes are idempotent `PUT` create-or-replace: `201`
    create, `200` replace, no `409`; the corp-scoped lookup doubles as the
    tenancy check (foreign resource → `404`).

## API contract & type generation

- All routes live under **`/api/v1`**; breaking changes would ship as `/api/v2`.
  (ADR-0011)
- API DTOs live in `schemas/`; the interface layer maps records/results to them.
  The internal model shape and the public contract stay decoupled. (ADR-0018)
- **Type-gen workflow after any DTO change**: `uv run python -m app.openapi_export`
  (writes the committed `frontend/openapi.json`) → `npm run gen:api` (writes
  `src/api/schema.d.ts`). Generate from the committed file, never a live URL.
  Never hand-edit `schema.d.ts`; import the friendly aliases from
  `src/api/types.ts`. Hand-written response types only for trivial endpoints
  without a response model (e.g. `/health`). (ADR-0011, ADR-0023)

## Auth, tenancy roles & security

- **Session**: EVE SSO authorization-code + PKCE + `state`, code exchanged
  server-side; the app issues its own httpOnly, Secure, SameSite=Lax cookie. The
  cookie carries stable **identity only** (`SessionIdentity`: ids/names, `is_ceo`,
  `is_director` as of login) — never the resolved role. (ADR-0004, ADR-0016)
- **Roles**: exactly three, strictly ordered `member < manager < ceo`; enforce
  with the `require_role(min_role)` dependency. CEO is derived from ESI
  (`ceo_id` comparison), never stored as an assignment row; manager is a
  `ManagerAssignment` row. Don't use EVE in-game roles for day-to-day
  authorization — Director gates only one-time registration. (ADR-0005, ADR-0015)
- **Resolve the role from the DB on every request** (`resolve_role` /
  `get_current_user`), so a revoke takes effect on the next request. Never
  rewrite the cookie on role changes. `RequireIdentity` is only for handlers that
  need just the stable identity. (ADR-0016)
- **App-admin is a separate axis** *(proposed)*: decided in exactly one place
  (`domain/app_admin.py: is_app_admin`), gated by `require_app_admin`, endpoints
  in their own `interface/v1/admin/` namespace, never mixed into corp routers or
  the role ordering; `/me`'s `is_app_admin` is cosmetic nav-gating only. (ADR-0041)
- **Entitlement gating is data, not code** *(proposed)*: every gated use case
  checks the entitlement in the application layer and raises a typed
  `EntitlementRequired`; frontend hiding is cosmetic, never the gate. (ADR-0042)
- **CSRF**: every mutating request under `/api/` must carry the
  `X-Buyback-CSRF` header (middleware returns 403 without it); the frontend sends
  it via the `apiSend` wrapper. Never make `GET`/`HEAD`/`OPTIONS` mutate state —
  they're exempt from the check. Never add CORS middleware; same-origin is an
  invariant. (ADR-0017)
- **Public share surfaces expose the minimum** (link-unfurl preview: value +
  location only, never a character name); unknown ids return the generic shell
  with HTTP 200 (don't reveal existence); HTML-escape injected values; no
  User-Agent sniffing. (ADR-0040)

## EVE tokens

- **Login is token-free server-side**: no ESI access/refresh tokens persisted for
  identity; identity re-derives from public ESI at login. The "Open in EVE" login
  refresh token lives Fernet-encrypted **inside the session cookie only** — never
  the DB — and the cookie is re-sealed after any refresh (EVE rotates refresh
  tokens on use). (ADR-0004, ADR-0038)
- **Exactly one persisted per-corp ESI token** ("Corp ESI access"). A new
  corp-level ESI need folds its scope into `eve_corp_token_scopes` (deduped) —
  never a second persisted token, never extra scopes on normal login. (ADR-0036)
- **Refresh tokens are stored Fernet-encrypted only** (key
  `BUYBACK_TOKEN_ENCRYPTION_KEY`); access tokens are used for the call and
  dropped, never persisted. After every refresh grant, re-encrypt and save the
  (possibly rotated) refresh token. (ADR-0029)
- **Distinguish failure kinds**: `invalid_grant` means the refresh token died —
  flag `last_refresh_failed_at`. A per-scope/role **403 is not a token failure** —
  log and skip, never set the flag (roster, contracts, assets, divisions all
  follow this). Cosmetic reads (division names) degrade to fallbacks, never
  error. (ADR-0029, ADR-0036, ADR-0037, ADR-0048)
- **Token-bearing code logs `repr(exc)` only, never `exc_info`** — no token
  material in logs. (ADR-0037)
- **Privacy page rule**: any change to how a token is used, stored, scoped, or
  refreshed updates `frontend/src/pages/Privacy.tsx` + its test in the same
  change. (`CLAUDE.md`)

## Market data & caching

- **One read-through entry point**: all market reads go through
  `application/market.py::get_market_prices`; the source branches on
  `domain/market.resolve_market_source(hub)` (`fuzzwork` for the five NPC hubs,
  else ESI) — never a user-facing source choice, never a parallel entry point.
  (ADR-0006, ADR-0028)
- **Two cache tiers**: the `market_prices` table is the durable L2 (keyed by
  `(hub_id, type_id)` only — token selection decides who fetches, never what is
  cached). The pluggable L1 (`plugins/cache.py`) is best-effort: bytes-pure
  memcached-shaped port (`get`/`set`/`delete`/`aclose`, `safe_key`,
  `get_model`/`set_model`), backend chosen only via `build_cache(settings)` /
  `BUYBACK_CACHE_BACKEND`, built once in the lifespan and injected. Only promote
  **fresh** data into L1; timeouts/transport errors degrade to miss/no-op;
  `cache=None` must keep working. (ADR-0033, ADR-0034)
- **Degrade, never fail**: source outages serve stale L2 or reject individual
  lines ("No market data"); a failing per-rule hub degrades only its own lines.
  One documented exception fails closed: a structure hub whose corp token is
  missing/flagged raises `StructureMarketUnavailable` rather than silently
  unpricing lines. (ADR-0006, ADR-0028, ADR-0031, ADR-0034)
- **ESI protection**: the concurrency cap is one **process-wide**
  `asyncio.Semaphore` created in the lifespan and injected into every
  `EsiMarketClient` — never per-call in production paths. Appraisals reject above
  `max_esi_types_per_appraisal` distinct ESI-priced types (422) before pricing.
  Keep the `X-Esi-Error-Limit-Remain` backoff. One type's failure is logged and
  skipped, never fatal. (ADR-0028, ADR-0035)
- **Aggregation is pure Decimal functions** in `domain/aggregates.py`,
  reproducing Fuzzwork semantics so cached rows are source-interchangeable; an
  empty order side is an all-zero aggregate with `order_count = 0`. (ADR-0028)
- Hub resolution (station → region/label) happens at **config/rule save time**
  from the seeded SDE (reject unknown → 422); the hot pricing path never calls
  ESI universe endpoints. Rule hub overrides are the same nullable quartet as
  config, all-null = inherit; the corp default never enters the domain layer —
  the application substitutes it. (ADR-0028, ADR-0031)

## Background jobs

- **In-process only** — APScheduler from the FastAPI lifespan; no
  Celery/Redis/broker. Known limit: multiple replicas would duplicate work.
  (ADR-0010, ADR-0034)
- **Wiring in `interface/jobs.py`; the work itself is a use case** (e.g.
  `application/market_refresh.py`). (ADR-0034)
- **Best-effort per unit of work**: a failing hub/corp is logged and skipped,
  others continue; commit per unit so partial progress survives; a top-level
  guard keeps recurring jobs alive. Compute "due" from existing data
  (`fetched_at` vs cutoff) — no tracking tables. (ADR-0034)

## Frontend

- **TanStack Query for all server state** (no Redux/Zustand for it); invalidate
  after mutations. React Router for navigation. API access is a thin `fetch`
  wrapper with `credentials: "include"` + the generated types — no bespoke SDK;
  mutations go through `apiSend` (CSRF header). (ADR-0013, ADR-0017)
- **Pico.css via semantic HTML**; add classes sparingly (`.isk`, `.num`,
  `.rejected`); app tweaks in `src/index.css`. (ADR-0023)
- **Money stays a string end-to-end**: Decimal fields generate as `string`;
  format with `lib/format.ts`, never `Number()`. (ADR-0023)
- **Reuse shared pickers/components** where one exists (e.g. `HubPicker`
  wherever a hub is chosen). (ADR-0031)
- **Accounting UI speaks plain English** *(proposed)*: never surface *lot*,
  *FIFO*, *COGS*, *NRV*, or *reconciliation* — "Profit", "What the buyback has
  now", "What we paid for it". (ADR-0043–0045)

## Accounting ledger rules *(proposed — ADR-0042…0047)*

- The lot ledger is the source of truth; ESI data (assets, wallet) reconciles
  against it, never replaces it. (ADR-0044)
- **Derived, never stored**: landed unit cost
  (`unit_purchase_cost + unit_hauling_cost`, floored to `written_down_to`) and
  lot state (`qty_idle = qty_remaining − allocations`) are computed, not columns.
  (ADR-0043)
- **FIFO consumption is a pure `domain/` function**; each sale writes one
  `sales` row per lot touched, snapshotting `unit_cost` and `cost_is_estimated`
  at sale time — never derive COGS from the lot at read time. (ADR-0043)
- **Never write up**: write-downs set `written_down_to = NRV` and book the loss;
  matched stock keeps its recorded cost; transformation children conserve cost
  (`Σ child costs = source cost consumed`, allocated pro-rata by market value at
  split-off), gains stay unrealized until sale. (ADR-0043, ADR-0044, ADR-0047)
- **Two independent flags, never conflated**: `source` (esi | manual | …) is
  provenance; `cost_is_estimated` is cost confidence. Estimated cost propagates
  through FIFO; reports segment by it. (ADR-0043, ADR-0045)
- **Idempotency everywhere**: lot creation keyed on `appraisal_id`; ingestion
  keyed on wallet `transaction_id` / `contract_id`; reconciliation deemed-lots on
  the `(location, type)` delta. Re-polling never double-records. (ADR-0043–0045)
- **Corrections are reversing entries** pointing at what they reverse — never
  edits or deletes of booked entries. Shortfalls/anomalies are flagged for a
  human ("Needs a look"), never auto-resolved. (ADR-0044, ADR-0045)

## Testing

- Add tests alongside new behavior; unit/integration suites are the primary net.
- Backend tests run against a dedicated `<name>_test` database; dispose the
  engine pool per test (asyncpg connections must not outlive their event loop).
  (ADR-0024)
- The backend test client sends a default `X-Buyback-CSRF` header. (ADR-0017)
- Tenant isolation gets explicit cross-corp access tests. (ADR-0003)
- **E2E** *(proposed)*: Playwright smoke pack only — serial, Chromium, one or two
  journeys per feature surface. Targets the single deployable
  (`BUYBACK_STATIC_DIR=frontend/dist`, never the Vite dev server); auth via
  minted session cookies from `e2e/support/e2e_setup.py` (never reimplement
  signing in Node); owns the `buyback_e2e` DB, dropped/recreated per run. CI
  must not pull the real SDE seed from Fuzzwork. (ADR-0046)
- The pre-commit hook (`.githooks/pre-commit`) runs ruff + pytest for `backend/`
  changes and tsc + eslint + vitest for `frontend/` changes; e2e runs in CI only.

## Style

- Match the style of surrounding code — comment density, naming, idiom.
- **No multi-line list comprehensions**: extract a small named helper so it
  collapses to one line, or use a plain `for` loop. (`CLAUDE.md`)
- Backend handlers and outbound I/O are `async` throughout; outbound HTTP uses
  httpx (async) with a descriptive `User-Agent` on EVE-facing calls. (ADR-0001,
  ADR-0006)
- Keep Pydantic DTOs separate from SQLAlchemy models — no shared classes.
  (ADR-0001)

## Process

- **Versioning**: one number, bumped by CI on merge (`version-bump.yml`,
  `[skip ci]` commits). Never touch `backend/app/_version.py` in a PR. (ADR-0032)
- **ADRs**: new decisions supersede old ones (mark `Superseded by NNNN`) rather
  than editing history. Significant architectural choices get an ADR.
- **Config & secrets**: everything via env (`pydantic-settings`, prefix
  `BUYBACK_`); keep `.env.example` current; never commit secrets. Frontend base
  URL via `VITE_API_BASE_URL`, never hardcoded. (ADR-0012, ADR-0027)
- **When you scaffold or change conventions**: update `CLAUDE.md`
  (Layout/Commands), the relevant layer `CLAUDE.md`, and this file in the same
  change. (ADR-0019)
