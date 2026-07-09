# 0046. End-to-end smoke tests: Playwright against the single deployable

- **Status:** Proposed
- **Date:** 2026-07-09
- **Relates to:** [ADR-0012](0012-single-deployable-packaging.md) (the packaging under test),
  [ADR-0004](0004-eve-sso-session-auth.md) / [ADR-0016](0016-per-request-role-resolution.md)
  (the session cookie + role model the auth fixture mints against),
  [ADR-0017](0017-csrf-custom-header.md) (middleware exercised over real HTTP),
  [ADR-0024](0024-postgresql-database.md) (the dedicated e2e database mirrors the pytest
  `<name>_test` derivation), [ADR-0041](0041-app-admin-authorization-axis.md) (first
  feature journeys planned)

## Context

The backend suite exercises the ASGI app in-process; the frontend suite runs against
mocked API modules. Neither reaches the seam between them: a **real browser** speaking
**real HTTP** through the middleware stack — HttpOnly session cookies, the CSRF header,
`ApplicationError` mapping, React Query against actual responses, and the SPA as actually
served. Manual verification of the admin UI (#148) worked exactly at that seam and caught
a bug (expiry dates rendered in local time, not UTC) that neither suite could have seen.
We want that verification repeatable, not artisanal.

## Decision

**A small Playwright smoke suite (`e2e/`) drives a real browser against the single
deployable, using minted session cookies and a dedicated `buyback_e2e` database.**

- **Test the production packaging, not the dev servers.** The suite launches **one
  process**: uvicorn with `BUYBACK_STATIC_DIR=frontend/dist` (ADR-0012). No Vite, no
  proxy — every run also proves the served-SPA packaging. The SPA must be built first;
  global setup fails with instructions if `dist/` is missing.
- **Auth = minted sessions, EVE SSO out of the loop.** A Python bootstrap
  (`e2e/support/e2e_setup.py`, run inside the backend env) signs `buyback_session`
  cookies with the app's **own** signer and the e2e secret; the `loginAs(persona)`
  fixture injects them via Playwright's `context.addCookies()` (which, unlike page JS,
  can set HttpOnly cookies). Signing is never reimplemented in Node. We test the app
  behind login — CCP's OAuth is not ours to test.
- **Dedicated database, rebuilt per run.** Global setup drops/recreates `buyback_e2e`
  (name derived from the configured URL, mirroring pytest's `<name>_test`), runs **the
  real alembic migrations** (smoke-testing them too), and seeds deterministic fixture
  corps/personas. Dev data and `buyback_test` are untouched.
- **Smoke pack, not a third pyramid layer.** Serial (`workers: 1`), a handful of
  journeys per feature surface; the unit/integration suites remain the primary net. New
  features add one or two journeys, not parallel coverage.
- **CI: a separate job** with a Postgres service container, built SPA, and cached
  Playwright Chromium; trace/screenshot artifacts retained on failure. Advisory at
  first; made a required check once proven flake-free.

## Consequences

- The browser/HTTP seam is regression-tested; feature PRs with a UI surface get a place
  to encode the manual verification they'd otherwise repeat (first up: the #148 admin
  journeys once that PR lands).
- The cookie-minting script doubles as a **dev login tool** — mint a cookie and browse
  locally as any character/role without EVE SSO.
- New dev dependency (`@playwright/test`) and a new CI job (~2–4 min). The `e2e/`
  workspace is npm-managed but outside `frontend/`; the pre-commit hook does not run it
  (CI does).
- Journeys needing market data (appraise flow) are **deferred** until a small checked-in
  SDE fixture exists — the real SDE seed pulls from Fuzzwork, which CI must not do.
- Playwright runs Chromium only for now; cross-browser coverage is out of scope for a
  smoke pack.

## Alternatives considered

- **Vite dev server + proxy as the test target** — tests a topology users never run and
  needs two processes; the single deployable is both more realistic and simpler. Rejected.
- **Fake EVE SSO server to test the login flow itself** — heavier moving part to own,
  and the OAuth dance is CCP's contract, not ours; the session cookie is the app's real
  trust boundary. Rejected (revisit only if login-flow regressions actually bite).
- **Reimplement itsdangerous signing in Node** — a second implementation that can drift
  from the real one; minting stays in Python inside the backend env. Rejected.
- **Cypress** — Playwright has first-class trace tooling, `addCookies` for HttpOnly, and
  a built-in `webServer` manager; no counterweight in Cypress's favor here. Rejected.
- **Schema-recreate instead of migrations** (like conftest) — running `alembic upgrade
  head` costs little and smoke-tests the migration chain on every run. Rejected.
