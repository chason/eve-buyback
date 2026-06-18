# 0036. Designate Buyback Managers by searching a synced corp roster

- **Status:** Accepted
- **Date:** 2026-06-18
- **Relates to:** [ADR-0005](0005-authorization-roles.md) (member/manager/CEO roles),
  [ADR-0015](0015-corp-registration.md) (Director detection at login),
  [ADR-0016](0016-per-request-role-resolution.md) (instant grant/revoke),
  [ADR-0004](0004-eve-sso-login-session.md) (login stays minimal-scope, token-free),
  [ADR-0029](0029-structure-market-access.md) (the SSO step-up pattern this mirrors)

## Context

A CEO or Director needs to **designate a character as a Buyback Manager** (who may then edit
config and pricing rules) by **searching the corporation's members**. The grant machinery
already exists — `manager_assignments` + the `add_manager`/`remove_manager`/`list_managers`
use cases, with `add_manager` validating the target is in the corp via `esi.get_character` —
but three gaps remained: the endpoints were **CEO-only**; there was **no way to search corp
members** (ESI has no public fuzzy character search); and there was **no UI**.

A real "filter to corp members" picker needs the corp's actual member list. That list comes
only from ESI `GET /corporations/{id}/members/`, which requires the scope
`esi-corporations.read_corporation_membership.v1` **and** a token whose character holds the
in-game Director role. The normal login is deliberately minimal-scope and persists no token
(ADR-0004), so the roster needs its own authorization.

## Decision

**A dedicated, CEO/Director-gated "Sync corp roster" SSO step-up that caches a roster
snapshot — without persisting a refresh token.**

- **Scope, off the normal login.** A new `eve_roster_scopes` is requested only by this flow,
  so ordinary members never consent to membership-read. The shared EVE redirect is routed
  client-side by the OAuth `state` prefix (`roster.`, alongside login and `structure.`).
- **Fetch + cache, don't persist.** On completion we exchange the code, call
  `get_corporation_members` with the **transient** access token, resolve names via the public
  bulk `POST /universe/names/`, and replace the corp's rows in a new `corp_roster_members`
  table (`{character_eve_id, name, synced_at}`). The token is then discarded. The picker
  searches that table server-side (`ILIKE`, ordered, limited to 25) — never shipping the
  whole roster to the client.
- **Directors may designate.** A new `require_ceo_or_director` interface dependency gates the
  roster endpoints and relaxes the existing manager endpoints from CEO-only. Directors
  administer who the managers are even when they aren't managers themselves.
- **The grant re-checks at ESI.** `add_manager` still verifies the target's current corp via
  `esi.get_character`, so a **stale roster can never grant an outsider** — the roster is a
  search index, not the authority.

## Consequences

- Membership-read consent is confined to CEOs/Directors; the login scope set is unchanged.
- No new encrypted-secret-at-rest: unlike structure tokens (ADR-0029), nothing is persisted,
  so there is no refresh/rotation/failure-surfacing machinery to carry.
- **Manual freshness.** The roster is only as fresh as the last sync. Corp membership changes
  slowly and the grant re-checks at ESI, so a manual re-sync (with a "synced N ago · M
  members" status + button) is acceptable. After first consent EVE auto-approves, so a
  re-sync is a quick redirect, not a full re-consent.
- `character_eve_id` is a `BigInteger` — the roster ingests **every** member, including high
  (>2³¹) character ids the login-only `characters` table may never have seen.
- Grant/revoke still takes effect on the target's next request (ADR-0016); no session change.

## Alternatives considered / deferred

- **Persist the refresh token + auto-refresh the roster** (via the background scheduler,
  ADR-0034) for zero-touch freshness — the structure-token shape. Heavier (a second
  encrypted-token subsystem) for a list that changes slowly; **deferred** as a follow-up.
- **Membership scope on the normal login**, fetching the roster opportunistically when an
  admin logs in — pollutes every member's consent screen and ties freshness to admin logins.
  Rejected on consent hygiene.
- **Exact-name lookup with no new scope** (resolve a typed name, validate corp membership on
  grant) — ships without the scope but is a lookup, not a browse-the-roster typeahead. Not
  chosen; the roster picker was the requested experience.
