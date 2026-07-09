# 0042. Paid accounting add-on: hosted-only per-corp entitlements + ISK payment reconciliation

- **Status:** Proposed
- **Date:** 2026-07-09
- **Relates to:** [ADR-0041](0041-app-admin-authorization-axis.md) (the admin who grants/revokes),
  [ADR-0012](0012-single-deployable-packaging.md) (self-hostable — why the gate is data, not code),
  [ADR-0003](0003-multi-tenant-corp-scoping.md) (per-corp entitlement fits corp scoping),
  [ADR-0029](0029-encrypted-refresh-token-structures.md) /
  [ADR-0036](0036-corp-roster-manager-designation.md) (tenant corp tokens the operator token is
  distinct from), [ADR-0034](0034-background-market-refresh.md) (the scheduler the reconciliation
  job rides), [ADR-0043](0043-lot-based-buyback-accounting.md) (the feature being gated)

## Context

We want to charge corps an **ISK** fee for access to the accounting module
(ADR-0043/0044/0045) on the **hosted** instance (`buyback.chason.cloud`). The app is
self-hostable (ADR-0012), so a code-level feature flag is meaningless for self-hosters — they
run the source and can flip it. And EVE has no payment API: ISK moves in-game only, so
"charging" means detecting an in-game ISK transfer and reacting to it. The operator also wants
recurring billing **and** hands-on manual control.

## Decision

**Gate the accounting module behind a per-corp entitlement that is data, not code; grant it
either by an app admin (ADR-0041) or by matched ISK payment; self-host grants itself via the
same admin path.**

- **The entitlement is a row.** `entitlements(corp_id, feature, granted_at, expires_at,
  source: 'payment' | 'admin')`. Active predicate (domain): `expires_at IS NULL OR expires_at
  > now`. Perpetual grants use a NULL expiry; payment grants carry a concrete expiry that
  reconciliation extends.
- **Enforced in the application layer.** Every accounting use case checks
  `corp_has_entitlement(corp, "accounting")` and raises a typed `EntitlementRequired` error;
  the interface maps it to HTTP 402/403 (register in `interface/errors.py`). UI hiding is
  cosmetic — the use-case check is the gate.
- **Two triggers, one mechanism.** *Admin grant* (`source=admin`) — the operator sets/extends/
  revokes by hand; this is also how **self-host** turns the feature on (add your character to
  the admin allowlist, grant your own corp), which is why there is no separate `config` source.
  *Payment grant* (`source=payment`) — extended automatically by reconciliation.
- **Recurring ISK via operator-wallet reconciliation.** The operator holds their **own** EVE
  wallet token; a background job (ADR-0034 scheduler) reads the operator's wallet journal,
  matches an incoming ISK entry by amount + sender corp (+ a per-corp reference shown at
  checkout), and extends that corp's entitlement expiry. Unmatched payments surface in the
  admin UI for a manual match.
- **The operator token is separate from tenant tokens.** It is a **new persisted token the
  operator holds** (their character/corp wallet), unrelated to the per-corp Corp ESI tokens
  (ADR-0029/0036). New scope + persisted token → **the Privacy page must describe it** (project
  rule; the page is kept accurate to the token ADRs).
- **Hosted-only by construction.** Nothing gates self-hosters in code; the `entitlements` table
  is simply empty until an admin grants a row.

## Consequences

- Monetization without licensing — stays friendly to the open/self-hostable posture (ADR-0012).
- **Manual-first is shippable:** the gate + admin grants + the `entitlements` table are a clean
  first slice (flip entitlements by hand); automated ISK reconciliation is a later slice added
  once the feature has proven worth paying for.
- Depends on the admin axis (ADR-0041). Price (ISK/period) is a config value.
- ISK matching is best-effort — memo collisions, partial or early payments — so the admin UI
  always allows manual match/extend/revoke as the fallback path.
- The Privacy page + its test gain the operator wallet token in the same change that adds it.

## Alternatives considered

- **License keys enforced on self-hosters** — heavy and pointless against editable source;
  rejected (mirrors ADR-0041).
- **A `config` entitlement source for self-host** — redundant: a self-hoster is just an admin
  granting their own corp, so the admin path already covers it. Dropped to keep one grant
  mechanism.
- **One-time ISK fee** — ISK inflation erodes it; recurring is preferred. (The `expires_at`
  model supports either; a perpetual admin grant is a NULL expiry.)
- **Charging real money** — out of scope and needlessly heavy; an ISK fee is a natural in-game
  sink the corp already understands. (We *accept* ISK for a service — not sell ISK, which would
  breach EVE's ToS.)
- **Gating in the frontend only** — trivially bypassed; the application-layer check is
  authoritative and the UI hide is convenience.
