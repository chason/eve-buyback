# 0038. "Open in EVE" via a session-held login refresh token

- **Status:** Accepted
- **Date:** 2026-06-19
- **Amends:** [ADR-0004](0004-eve-sso-session-auth.md) (login previously persisted **no**
  token; it now keeps an encrypted refresh token **in the session cookie**)
- **Relates to:** [ADR-0037](0037-corp-contract-watcher.md) (the matched contract this
  opens), [ADR-0029](0029-encrypted-refresh-token-structures.md) (the `TokenCipher` reused),
  [ADR-0016](0016-per-request-role-resolution.md) (the session cookie payload)

## Context

The contract watcher (ADR-0037) links each appraisal to its real in-game contract. The
natural next step is a one-click **"Open in EVE"** that pops that contract open in the
manager's running client, via ESI `POST /ui/openwindow/contract/` (scope
`esi-ui.open_window.v1`).

That endpoint opens the window in the client of **whoever's token makes the call** — so it
must use the **logged-in user's own** token, not the corp ESI token (ADR-0029/0036), which
belongs to one CEO/Director and would open the contract in *their* client, not the clicker's.

But ADR-0004 deliberately keeps login **token-free**: we read the character's roles once at
login and discard the access token, persisting nothing. To call open-window on demand we
need that character's token available **after** login — without standing up a server-side
token store for every member.

## Decision

**Keep the login refresh token encrypted in the user's own session cookie, and use it to
call open-window on demand.** This amends ADR-0004's "no persisted login token" to "no
*server-side* login token".

- **One new login scope.** `eve_scopes` gains `esi-ui.open_window.v1`. Users who logged in
  before this **re-login** to grant it; until then the feature is simply hidden for them.
- **Token lives only in the cookie.** `complete_login` now Fernet-encrypts the refresh
  token (the existing `TokenCipher`, ADR-0029) and stores the ciphertext in the signed,
  http-only `SessionIdentity` cookie — **never in the database**. The access token is still
  discarded after reading roles. The token dies with the session; there is no server-side
  store to leak, revoke, or migrate. This is the closest thing to ADR-0004's original
  promise: the secret lives only in the user's own encrypted cookie.
- **On-demand, per click.** `POST /api/v1/appraisals/{public_id}/open-contract` resolves the
  appraisal's matched contract **scoped to the caller's corp** (a corp can't open another's
  contract), decrypts the session token, refreshes it server-side for a fresh access token,
  and calls open-window. EVE **rotates** the refresh token on use, so the response **re-seals
  the cookie** with the new ciphertext.
- **Every failure means "log in again".** No session token (pre-feature login), a revoked
  grant, or a token without the scope all collapse to one `OpenContractUnavailable` (409)
  whose message tells the user to re-login. A `can_open_contract` flag on `/me` (true once
  the session holds a token) hides the button otherwise, so the error is the exception, not
  the norm.

## Consequences

- A manager opens the exact contract in **their** client in one click — no manual search.
- **The cookie now carries a secret.** It's Fernet ciphertext (opaque without the
  server-side key) in a signed, http-only cookie, so it's not readable by JS or forgeable;
  a stolen cookie could at worst trigger an open-window in the victim's own client (a
  nuisance, not a credential leak) and can't be decrypted. The cookie grows by a few hundred
  bytes — well within the 4 KB limit.
- **Token-use changes are disclosed.** Per the repo convention, the Privacy / Data Use page
  and its test are updated in this change to describe the session-held login token + the
  open-window scope.
- Re-login friction: existing users must log in once more to gain the scope (the button is
  hidden, not broken, until they do).
- The login flow now depends on `TokenCipher`, so the token-encryption key matters for login
  too — but it already has a (dev-only) default and is required in production for ADR-0029.

## Alternatives considered / rejected

- **Use the corp ESI token (ADR-0029/0036)** to open the window: opens the contract in the
  CEO/Director's client, not the clicking manager's — wrong client, and couples a per-user
  action to a shared corp credential.
- **A server-side login-token store** (encrypted in the DB, like the corp token): reintroduces
  exactly the persisted-login-token subsystem ADR-0004 avoided, for an action that only needs
  the token transiently. The cookie-held token needs no store, no cleanup, and dies with the
  session.
- **A transient SSO step-up** per click (re-authorize just to open a window): an OAuth
  round-trip and redirect every time the user clicks — far too heavy for a convenience button.
- **Deep-link / `contract:` URL instead of ESI**: EVE has no reliable client deep-link for a
  specific contract; the ESI open-window endpoint is the supported path.
