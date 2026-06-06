# 0016. Resolve the app role from the database per request

- **Status:** Accepted
- **Date:** 2026-06-07
- **Amends:** [0005](0005-authorization-roles.md) (role *resolution timing*)

## Context

[ADR-0005](0005-authorization-roles.md) defined the member / Buyback Manager / CEO
roles, and [ADR-0004](0004-eve-sso-session-auth.md) stores the authenticated user in
a signed session cookie that lives up to 8 hours. The original implementation baked
the resolved `role` **into the cookie at login**. That means a manager grant or
revocation does not take effect until the affected user re-logs in or their cookie
expires — up to an 8-hour window in which a revoked manager keeps manager rights.

That is harmless while roles gate little, but [ADR-0007](0007-pricing-rule-taxonomy.md)
(M5) will hang real pricing-edit privileges off `manager`. We want revocation to be
effective immediately, with the database as the single source of truth.

A constraint shapes the design: **`is_ceo` for an _unregistered_ corp cannot be
derived from our database** — there is no `Corporation` row yet, so the corp's
`ceo_id` is unknown until ESI tells us at login. The same is true of the EVE
`Director` flag, which needs an access token we deliberately do not persist
([ADR-0004](0004-eve-sso-session-auth.md)).

## Decision

Split the cookie into **stable identity** and **resolved authorization**:

- The cookie carries identity only — `SessionIdentity`: character/corp ids and
  names, plus `is_ceo` and `is_director` as established from ESI **at login**. These
  change rarely and cannot be re-derived per request without a token.
- The **app role** (`member`/`manager`/`ceo`) and `corporation_registered` are
  **resolved from the database on every request** by an async dependency
  (`resolve_role` / `get_current_user` in `app/auth/session.py`):
  - `is_ceo` (from the cookie) → `ceo`;
  - else a `ManagerAssignment` row exists → `manager`;
  - else `member`. `corporation_registered` = a `Corporation` row exists.
- `require_role(min)` and `RequireUser` depend on this fresh resolution, so every
  gate reflects the current database state. `RequireIdentity` is available for
  handlers that need only the stable identity (e.g. `corporation_id`).

## Consequences

- **Manager grant/revoke is effective on the caller's very next request** — the
  security-critical, frequently-changing privilege is now always current. Proven by
  `tests/test_roles.py`.
- Registration no longer rewrites the cookie: a Director who registers (and is
  auto-granted manager per [ADR-0015](0015-corp-registration-ceo-or-director.md))
  simply resolves as `manager` on the next request. The old `model_copy` cookie
  rewrite in the registration handler is removed.
- Cost: one or two indexed reads per authenticated request (a `Corporation` get and,
  for non-CEOs, a `ManagerAssignment` lookup). Negligible at this scale.
- **Residual freshness gap (accepted):** CEO succession, Director status, and a
  member *leaving the corp entirely* still re-resolve only at next login, because
  they depend on ESI. This is unchanged from before and acceptable — these change
  rarely and are not the revocation case that matters for day-to-day authorization.
- Force-logout / revoke-an-arbitrary-session is still out of scope: sessions remain
  stateless-in-cookie ([ADR-0004](0004-eve-sso-session-auth.md)). That would need a
  server-side session store and is deferred until a concrete need arises.

## Alternatives considered

- **Keep the role in the cookie, accept ≤8h lag** — cheapest, but a revoked manager
  retains rights for hours; unacceptable once M5 gives `manager` teeth.
- **Server-side session store (DB/Redis) for true revocation** — enables
  force-logout of any session, but reintroduces stateful sessions and cuts against
  the single-deployable, SQLite-only design ([ADR-0012](0012-single-deployable-packaging.md)).
  Overkill for the privilege-freshness problem at hand.
