# 0017. CSRF: SameSite=lax plus a required custom header

- **Status:** Accepted
- **Date:** 2026-06-07
- **Relates to:** [0004](0004-eve-sso-session-auth.md) (session cookie), [0012](0012-single-deployable-packaging.md) (single origin)

## Context

Authentication rides on a signed **cookie** ([ADR-0004](0004-eve-sso-session-auth.md)),
which browsers attach automatically — the classic setup for cross-site request
forgery. The cookie is already `SameSite=lax`, so browsers won't send it on
cross-site `POST`/`PUT`/`PATCH`/`DELETE`; a forged cross-origin mutation arrives with
no cookie and is rejected as unauthenticated. Login-CSRF is separately covered by the
OAuth `state` check. For the single-origin deployment
([ADR-0012](0012-single-deployable-packaging.md)) that is already a real defense.

But relying on `SameSite=lax` alone leaves it resting on unwritten invariants, and it
has soft spots: "same-site" includes **subdomains**, so a future subdomain-per-tenant
hosting model would let a hostile tenant page count as same-site; and a mutating `GET`
would bypass it entirely.

## Decision

Keep `SameSite=lax` as the primary boundary and add a cheap **defense-in-depth**
check: every state-changing API request must carry a custom header
`X-Buyback-CSRF`. A `BaseHTTPMiddleware` (`app/middleware.py`) returns `403` for any
`POST`/`PUT`/`PATCH`/`DELETE` under `/api/` that lacks it; safe methods and non-API
paths pass untouched. The SPA's `apiSend` wrapper attaches the header to all mutating
calls. Because a cross-origin caller cannot set a custom header without a CORS
preflight — which we never grant (no CORS middleware is configured) — the header
cannot be forged from another origin.

The supporting invariants are now explicit and must hold:

- **GET/HEAD/OPTIONS never mutate state** (they are exempt from the header check).
- **No permissive CORS** is configured; the same-origin model
  ([ADR-0012](0012-single-deployable-packaging.md)) is assumed.
- The OAuth `state` parameter remains the login-CSRF defense.

## Consequences

- Defense-in-depth that costs ~nothing at runtime (one header check per mutating
  request, no DB or crypto) and is a **one-time, flat cost on the backend**: the
  middleware automatically covers every future M5/M6 route.
- The frontend must route all mutating calls through `apiSend`; tests send a default
  `X-Buyback-CSRF` header on their client. Covered by `tests/test_csrf.py`.
- If we ever adopt **subdomain-per-tenant** hosting, revisit this: the header check
  still holds, but document whether `SameSite=lax` remains adequate as the primary
  boundary or should become `strict` / token-based.

## Alternatives considered

- **SameSite=lax only** — adequate for single-origin today, but undocumented and
  fragile against the subdomain case and accidental mutating GETs; we wanted the
  invariants codified and a second layer.
- **Double-submit cookie / synchronizer token** — more robust in the abstract but
  adds token issuance, a non-HttpOnly cookie, and compare logic for little gain over
  the header check in a single-origin app; rejected as over-engineered for now.
- **SameSite=strict** — tighter, but adds friction for inbound links and is fussier
  to reason about around the external IdP redirect; lax is the better default for
  flows that bounce through EVE SSO.
