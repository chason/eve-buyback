# 0015. Corp registration by CEO or Director

- **Status:** Accepted
- **Date:** 2026-06-05
- **Amends:** [0004](0004-eve-sso-session-auth.md) (scopes), [0005](0005-authorization-roles.md) (registration authority)

## Context

[ADR-0005](0005-authorization-roles.md) left open who may register a corporation.
The CEO is knowable from public ESI (`ceo_id`), but in practice the CEO is often
not the person administering tooling — Directors do that. We want a Director to be
able to onboard the corp, not just the single CEO character.

"Director" is an **EVE in-game corporation role**, not public data: reading it
requires the authenticated scope `esi-characters.read_corporation_roles.v1`. This
is a deliberate widening of the `publicData`-only stance in
[ADR-0004](0004-eve-sso-session-auth.md).

## Decision

Allow the **CEO or any Director** to register their corporation.

- Add the scope **`esi-characters.read_corporation_roles.v1`** to the SSO request.
  At login the backend uses the (transient) access token to call
  `GET /characters/{id}/roles/` and records whether the character is a **Director**.
  Consistent with ADR-0004, the token is still **not persisted** — roles are read
  once at login and the Director flag is stored in the session.
- Registration is permitted when `role == "ceo"` **or** `is_director` is true.
- When a **non-CEO Director registers**, they are **auto-granted Buyback Manager**
  so they can immediately configure the program; the CEO remains implicit admin.
- **Graceful degradation:** if the roles scope was not granted (older session, or
  the EVE app hasn't added the scope), the roles lookup fails closed —
  `is_director = false`. Login still succeeds; only the Director-based registration
  path is unavailable until the user re-authenticates with the scope.

## Consequences

- Operators must add `esi-characters.read_corporation_roles.v1` to their EVE app and
  members must re-consent to grant it; `BUYBACK_EVE_SCOPES` now defaults to both
  scopes.
- One extra authenticated ESI call per login (best-effort, swallows 403).
- The session carries an `is_director` flag and a `corporation_registered` flag in
  addition to the app `role`.
- Directors get a real onboarding path without making the app depend on in-game
  roles for ongoing authorization — day-to-day permissions still flow through the
  app's member/manager/CEO model ([ADR-0005](0005-authorization-roles.md)).

## Alternatives considered

- **CEO only** — keeps `publicData`-only and is simplest, but blocks the common case
  where a Director runs the tooling; rejected by product choice.
- **Map all permissions to EVE corp roles** — broader scope creep and ties app
  behavior to in-game role usage that varies per corp; we use the EVE role *only* to
  gate the one-time registration action.
