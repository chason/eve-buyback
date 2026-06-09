# 0029. Persisted, encrypted refresh token for structure-market access

- **Status:** Accepted
- **Date:** 2026-06-09
- **Amends:** [ADR-0004](0004-eve-sso-session-auth.md) (token-free, for this one capability)

## Context

Pricing at a **player-owned Upwell structure** needs `GET markets/structures/{id}/`,
an **authenticated** ESI endpoint requiring the `esi-markets.structure_markets.v1`
scope and a character with **docking access** to that structure (ADR-0028 left this to
a later phase). Unlike public NPC-station orders, this can't be served by a one-shot
token at login: appraisals happen long after, on the backend's own schedule, so we
must **persist** an EVE refresh token — exactly what [ADR-0004](0004-eve-sso-session-auth.md)
deliberately avoided ("removes a high-value secret store… and its encryption burden").
ADR-0004 anticipated this: *"If a future feature must call ESI for the user… this ADR
is superseded by one that adds encrypted refresh-token storage and scopes."* This is it.

## Decision

Add structure-market access as an **opt-in, manager-gated** capability that persists a
**single encrypted refresh token per corporation**, leaving the normal login flow
entirely token-free.

- **Separate authorization flow.** A distinct SSO round-trip
  (`POST /corporations/me/structure-token/authorize` → `…/session`) requests the
  structure scopes (`build_authorize_url(scopes=…)` — overriding the default login
  scopes) with its own session-cookie state/PKCE keys. Only a **Buyback Manager / CEO**
  may run it. Normal `/auth/login` is unchanged.
- **Encrypted at rest.** The refresh token is stored Fernet-encrypted
  (`structure_market_tokens.encrypted_refresh_token`) with a new
  `BUYBACK_TOKEN_ENCRYPTION_KEY`; only the ciphertext touches the DB. Outside
  development the app refuses to *authorize* (not boot) while the key is the public
  placeholder, so corps that never use structures aren't forced to set it.
- **Access tokens are never persisted.** `get_structure_access_token` decrypts the
  refresh token, calls EVE's refresh grant, and returns a fresh access token held only
  for the one ESI call — the same "use once, drop it" posture as login. EVE may
  **rotate** the refresh token on refresh; the rotated value is re-encrypted and saved
  (or the integration breaks after the first refresh).
- **Graceful expiry.** A revoked grant (`invalid_grant` / HTTP 400) flags the row
  (`last_refresh_failed_at`) and surfaces as "expired" in the status; a lost docking
  permission (ESI 403 at price time) degrades like any market outage (ADR-0028) rather
  than failing the appraisal. Either way the manager re-authorizes.

## Consequences

- One high-value secret now lives in the DB — encrypted, single-purpose (structure
  market reads), corp-scoped, and revocable (`DELETE …/structure-token`). The blast
  radius of a DB leak without the key is limited to ciphertext.
- A new **required production secret** (`BUYBACK_TOKEN_ENCRYPTION_KEY`) for corps that
  use structures; documented in `.env.example` and the Coolify runbook. Losing the key
  means re-authorizing (the stored tokens become undecryptable) — acceptable.
- Identity/auth (ADR-0004) stays token-free; this token is **only** for structure
  market data and never participates in login or role resolution.
- `character_id`/`name` of the authorizing pilot are recorded for audit + display.
- **Hub ids are now stored/transported as strings** (`market_prices.hub_id`,
  `buyback_configs.market_hub_id`, `appraisals.market_hub_id`, and the API/TS types).
  Player structure ids are 64-bit (beyond int32 and beyond JS's safe-integer range), so
  a string is the only representation that's correct everywhere. NPC station/region/type
  ids stay integers.

## Alternatives considered

- **Keep structures out of scope** (ADR-0028's stance) — simplest, but the user wants
  to price at their own structure, which is impossible without a stored token.
- **Store the token unencrypted** — rejected; it's a high-value credential and the
  whole reason ADR-0004 avoided persistence was the secret-store burden.
- **Per-character tokens / many per corp** — more flexible but unnecessary for the MVP
  (a corp prices at one structure hub); one token per corp keeps the model simple.
- **Hard-fail boot without the key** (like `SESSION_SECRET`) — rejected; structures are
  optional, so failing at the point of authorization is friendlier than forcing every
  deployment to configure a key it may never use.
