# 0041. App-admin authorization axis (instance operator, orthogonal to corp roles)

- **Status:** Proposed
- **Date:** 2026-07-09
- **Relates to:** [ADR-0003](0003-multi-tenant-corp-scoping.md) (per-corp row scoping this
  deliberately crosses), [ADR-0005](0005-authorization-roles.md) /
  [ADR-0016](0016-per-request-role-resolution.md) (the corp role model this is orthogonal to),
  [ADR-0015](0015-corp-registration-ceo-or-director.md) (director from ESI),
  [ADR-0042](0042-paid-accounting-entitlements.md) (the first consumer — entitlement admin)

## Context

The app's entire authorization model is **per-corp**: `member < manager < ceo` (ADR-0005),
plus `director` read from ESI (ADR-0015/0036). Every router, use case, and repository is
**corp-scoped** (ADR-0003) — a principal only ever sees their own corp's data. There is no
notion of "the operator of this hosted instance."

The paid accounting add-on (ADR-0042) needs exactly that: someone who can grant/revoke a
corp's entitlement, view incoming ISK payments, and see **across all corps** — the app's
first cross-tenant surface. That role does not fit the per-corp hierarchy (an app admin is
not a "super-CEO" of every corp), and it has a bootstrap problem: with no admin today, there
is no one to appoint the first admin.

## Decision

**Introduce an instance-level app-admin axis, orthogonal to corp roles, sourced from an
env-var allowlist of EVE character ids behind a single resolver seam.**

- **Env-var allowlist is the source of truth.** `BUYBACK_ADMIN_CHARACTER_IDS` (comma-separated
  EVE character ids). No bootstrap problem, identical for self-host and hosted, nothing
  persisted. The hosted operator lists their own character.
- **One resolver function is the only thing that knows how admin-ness is decided.**
  `domain/app_admin.py: is_app_admin(character_id) -> bool` reads the config allowlist today. A
  later DB-backed admin table unions into this one function (`character_id in allowlist or
  repo.is_admin(...)`) with no change to any caller.
- **A new interface dependency `require_app_admin`,** parallel to `require_role` (ADR-0005),
  checks the logged-in character via `is_app_admin`. It is **derived per request** from
  `character_id` + config — **not** stored in the session cookie — so revocation is immediate
  and the check is always current.
- **Orthogonal to corp roles.** An app admin carries no corp role; the two checks compose
  independently. The `member/manager/ceo` ordering is untouched.
- **`/me` exposes `is_app_admin`** so the SPA can show/hide the admin nav. This is cosmetic;
  `require_app_admin` on every admin endpoint is the real gate.

## Consequences

- **First cross-tenant surface.** Admin endpoints live in their own `interface/v1/admin/`
  namespace, each admin-gated, and are **never** mixed into the corp-scoped routers — keeping
  the per-corp isolation (ADR-0003) intact everywhere else.
- **Immediate revocation, no cleanup.** Remove a character from the allowlist (and, on hosted,
  restart) and their admin access is gone next request; nothing to prune.
- **Cheaply extensible.** Delegated admins later = add a table, a repo method, union it into
  `is_app_admin`, and add management endpoints — no rework of the gate or its consumers.
- **No token use.** The axis reads no ESI and stores no token, so the Privacy page is
  unaffected by this ADR (the *entitlement* work in ADR-0042 is where token use enters).
- Self-host and hosted run the identical mechanism; self-hosters simply list their own
  character to unlock the admin UI.

## Alternatives considered

- **Signed license keys, enforced even for self-hosters** — heavy, adversarial, and awkward
  for an EVE-scale audience; the app is open/self-hostable (ADR-0012) so code-level gating is
  meaningless anyway. Rejected (see also ADR-0042).
- **A DB-backed admin table from day one** — unnecessary for a single-operator instance;
  deferred behind the resolver seam, which makes adding it a one-line change.
- **Model admin as a fourth corp role above CEO** — wrong shape: it isn't corp-scoped, would
  pollute the strict `member<manager<ceo` ordering, and would leak into every corp-scoped
  query. Rejected.
- **Store `is_app_admin` in the session cookie at login** — goes stale on revocation and is
  trivially derivable from `character_id` + config, so there is no reason to persist it.
