# 0036. One Corp ESI access token (structure markets + corp roster); manager designation

- **Status:** Accepted
- **Date:** 2026-06-18
- **Amends:** [ADR-0029](0029-encrypted-refresh-token-structures.md) (the persisted
  structure-market token becomes the corp's single ESI credential)
- **Relates to:** [ADR-0005](0005-authorization-roles.md) (roles),
  [ADR-0015](0015-corp-registration-ceo-or-director.md) (Director detection),
  [ADR-0016](0016-per-request-role-resolution.md) (instant grant/revoke),
  [ADR-0004](0004-eve-sso-session-auth.md) (login stays minimal-scope, token-free),
  [ADR-0034](0034-background-market-refresh.md) (the scheduler the roster job rides on)

## Context

A CEO or Director needs to **designate a Buyback Manager** by **searching the
corporation's members**. The grant machinery already existed (`manager_assignments` +
`add_manager`/`remove_manager`/`list_managers`, with `add_manager` re-checking corp
membership at ESI), but the endpoints were CEO-only, there was no member search, and no
UI.

A real "filter to corp members" picker needs the corp's member list, which ESI only
returns from `GET /corporations/{id}/members/` — gated behind the scope
`esi-corporations.read_corporation_membership.v1` **and** the in-game **Director** role.
That requires a Director-authorized token. The app already persists exactly one
Director-grade credential per corp: the encrypted **structure-market** refresh token
(ADR-0029), with server-side refresh, rotation, and revoke. Standing up a *second* token
subsystem (or a transient roster-only SSO step-up) would duplicate all of that.

## Decision

**Broaden the one persisted structure-market token into the corp's single "Corp ESI
access" token, carrying both scope sets, and fetch the roster server-side with it.**

- **One grant, both scopes.** `begin_corp_esi_authorize` requests
  `eve_corp_token_scopes` = structure-market scopes **+** the membership scope (deduped).
  The grant stays **off normal login** (ADR-0004), so ordinary members never consent to it.
- **CEO/Director gate the connect/revoke; the token character can be any corp member.**
  A new `require_ceo_or_director` dependency gates connect/revoke (and the manager
  endpoints). `complete_corp_esi_authorize` validates the authorizing character is in the
  corp (`AuthorizingCharacterNotInCorporation`). The **roster only populates if that
  character is a Director** — `get_corporation_members` 403s otherwise, surfaced as
  `RosterAccessDenied`; the token still works for structure pricing. **Status + structure
  search stay manager-visible** (managers configure structure hubs).
- **Server-side roster fetch, no EVE round-trip.** `corp_roster.refresh_roster` reuses
  `get_corp_esi_access_token` (server-side refresh), calls `get_corporation_members`,
  resolves names via the public bulk `/universe/names/`, and replaces a cached snapshot in
  `corp_roster_members`. The picker searches that table server-side (`ILIKE`, limited).
- **Fresh automatically + on demand.** A daily background job (on the ADR-0034 scheduler)
  re-pulls every token-holding corp's roster; a manual "Refresh roster" button does the
  same on demand, **rate-limited to 15 min** (`RosterRefreshTooSoon`, 429) — the job
  bypasses the cooldown. A connect auto-populates the roster best-effort.
- **The grant still re-checks at ESI**, so a stale roster can never designate an outsider —
  the roster is a search index, not the authority.

## Consequences

- One credential, one connect flow (the Config "Corp ESI access" panel), covering both
  structure pricing and the roster — no second encrypted-token subsystem.
- A **members-403 is not a refresh failure**: it never sets `last_refresh_failed_at` (which
  means the refresh token itself died and breaks *both* uses); the non-Director case is a
  separate roster-status signal.
- Structure setup now needs a **CEO/Director** to connect (was any Buyback Manager), and the
  token character must be **in the corp** (was anyone with docking access). Deliberate
  tightening — the token is the corp's credential.
- `character_eve_id` in `corp_roster_members` is `BigInteger` (the roster ingests every
  member, including high `>2³¹` ids the login-only `characters` table never sees).
- Multi-instance caveat (ADR-0010/0034 already noted): the scheduler is in-process.

## Alternatives considered / rejected

- **Transient roster-only SSO step-up** (fetch members, discard the token): forces an EVE
  round-trip on every refresh and can't run in the background. Built first, then replaced.
- **Membership scope on the normal login**, fetched opportunistically when an admin logs in:
  puts the scope on *every* member's consent screen and ties freshness to admin logins.
- **Exact-name lookup** (no member list, validate-on-grant): needs no Director and no scope,
  but is a type-the-name lookup, not a browse-and-filter picker — not the requested UX.
- **Persisting a second, roster-only token**: duplicates the ADR-0029 machinery for no gain.
