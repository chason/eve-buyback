# 0005. Authorization roles: member / Buyback Manager / CEO

- **Status:** Accepted (registration authority amended by [0015](0015-corp-registration-ceo-or-director.md))
- **Date:** 2026-06-04

## Context

Three capability levels exist: any corp member may **view** buyback info and get
quotes; a **Buyback Manager** may **edit** pricing; the **CEO** administers who the
managers are and registers the corp. "Buyback Manager" is an app concept, not an
EVE in-game role, so it must be granted within our system. The CEO, by contrast,
is authoritatively known from EVE.

## Decision

Define three roles resolved at login from the session's `character_id` + `corp_id`:

- **CEO** — `character_id == corporations/{corp_id}.ceo_id` (from public ESI).
  Auto-granted, never stored; always current.
- **Buyback Manager** — a `ManagerAssignment(corp_id, character_id)` row exists,
  granted/revoked by the CEO.
- **Member** — authenticated and belongs to a registered corp; the default.

Enforce via a FastAPI dependency `require_role(min_role)` on each route. Roles are
strictly ordered (member < manager < ceo); the CEO implicitly has manager rights.

## Consequences

- No dependency on the `read_corporation_roles` ESI scope — keeps the SSO scope at
  `publicData` ([ADR-0004](0004-eve-sso-session-auth.md)).
- CEO succession in-game is reflected automatically the next login; revoking a
  former CEO needs no manual cleanup.
- Manager grants survive corp-role churn (they're ours), which is the desired
  product behavior.
- Edge case: a member who leaves the corp keeps a manager row that simply stops
  applying (corp scoping + membership re-check at login gate it). Provide CEO UI to
  prune stale managers.

## Alternatives considered

- **Map to EVE corp roles (Director, etc.)** — requires an extra scope and ties
  app permissions to in-game roles the corp may use differently; rejected.
- **Store the CEO as an assignment row** — would drift from in-game reality; using
  live ESI is authoritative.
