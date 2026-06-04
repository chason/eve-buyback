# 0004. EVE SSO login with backend-issued session cookie

- **Status:** Accepted
- **Date:** 2026-06-04

## Context

Users authenticate with **EVE SSO** (OAuth2). The frontend receives the SSO
`code` and sends it to the backend, which must establish identity (character,
corp) and an ongoing session. The app only needs to *identify* the user and read
public corp data — it does not (in MVP) act on the user's behalf via ESI.

## Decision

Run the **authorization-code flow with PKCE + `state`**. The backend is the
confidential client (holds `client_secret`), exchanges the code server-side,
verifies the token, and then issues **its own session** as an **httpOnly, Secure,
SameSite=Lax cookie**. EVE access/refresh tokens are **not persisted** — identity
and corp are re-derived from public ESI at each login. Request only the
`publicData` scope.

## Consequences

- Secrets and tokens never reach the browser; XSS can't read the session cookie.
- Not storing refresh tokens removes a high-value secret store from the MVP and
  its encryption/rotation burden ([ADR-0005](0005-authorization-roles.md) relies
  only on public ESI).
- Corp changes are picked up on next login (membership re-derived), which is
  acceptable; long-lived sessions could lag a corp move — keep session lifetime
  modest and re-verify on login.
- Cookie auth needs **CSRF protection** on mutations (SameSite=Lax + required
  custom header / CSRF token).
- If a future feature must call ESI for the user (wallet/contracts), this ADR is
  superseded by one that adds encrypted refresh-token storage and scopes.

## Alternatives considered

- **Stateless JWT bearer tokens in the browser** — convenient for third-party API
  consumers, but token storage in JS is XSS-exposed and revocation is awkward;
  revisit when non-browser consumers actually arrive ([ADR-0011](0011-api-contract-and-typescript-types.md)).
- **Redirect URI straight to the backend** — slightly fewer hops, but the chosen
  "frontend posts the code" flow matches the product description and keeps the SPA
  in control of post-login routing. Token exchange stays server-side either way.
- **Persisting refresh tokens now** — unnecessary for identity-only auth; adds risk.
