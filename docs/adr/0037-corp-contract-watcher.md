# 0037. Corp contract watcher → validated appraisal status

- **Status:** Accepted
- **Date:** 2026-06-19
- **Relates to:** [ADR-0014](0014-persisted-appraisals.md) (appraisals are write-once),
  [ADR-0029](0029-encrypted-refresh-token-structures.md) /
  [ADR-0036](0036-corp-roster-manager-designation.md) (the one Corp ESI access token this
  reuses), [ADR-0030](0030-buyback-drop-off-locations.md) (the delivery location matched
  against), [ADR-0034](0034-background-market-refresh.md) (the scheduler the job rides on),
  [ADR-0020](0020-decimal-money-values.md) (Decimal money),
  [ADR-0010](0010-in-process-scheduling.md) (in-process scheduler caveat)

## Context

A member gets an appraisal, then creates an in-game **item-exchange contract** to the corp
and pastes the appraisal's `public_id` into the contract **Description**
(`frontend/src/pages/Appraisal.tsx` already instructs this). A Buyback Manager accepts the
contract in EVE. Until now the app had no idea which appraisals became real, accepted
contracts — managers cross-referenced by hand, and a member could quietly contract the
wrong items or a wrong price against a fat appraisal.

We want each appraisal to show its **contract state** (In Progress, Completed, or a void
state) on both the Appraisals list and the detail page, ordered so the ones needing
attention float to the top. Crucially, a contract that merely *cites* an appraisal isn't
trusted: it must actually correspond to it.

## Decision

**A background job reads the corp's ESI contracts with the existing Corp ESI access token,
matches each to an appraisal by the pasted `public_id`, validates the match, and records
one status per appraisal in a separate mutable link table.**

- **Separate link table, never touch the appraisal.** Appraisals are immutable (ADR-0014),
  so status lives in `appraisal_contracts` (`appraisal_id` unique FK, `corporation_id`,
  `contract_id`, `status`, `issued_at`, `completed_at`), LEFT-joined at read time. The
  appraisal row is never mutated.
- **Reuse the one Corp ESI token (ADR-0036), one new scope.** The watcher calls
  `GET /corporations/{id}/contracts/` + `…/{contract_id}/items/` via
  `get_corp_esi_access_token` (server-side refresh). This needs
  `esi-contracts.read_corporation_contracts.v1` folded into `eve_corp_token_scopes`, so
  **existing corps must reconnect** (new grants include it); the Config panel shows a
  "reconnect to enable contract tracking" hint when the stored scopes lack it.
- **Match by public_id, then validate.** The contract title is tokenized into base64url
  runs and matched **case-sensitively, exactly** against the corp's `{public_id → id}` map
  (also any 12-char window inside a longer run). A cited appraisal is only confirmed when
  `contract_matches`: `price == accepted_total` **and** `start_location_id` equals the
  appraisal's delivery location **and** the contract's included items **exactly** equal the
  appraisal's accepted lines (same type ids + quantities, no missing, no extras). Money is
  parsed JSON-number → `Decimal` directly (ADR-0020).
- **Status derivation.** `derive_lifecycle_status` maps ESI status →
  `in_progress` (`outstanding`/`in_progress`), `completed` (`finished*`), the void states
  `rejected`/`cancelled`/`failed` as-is, or `None` for `deleted`/`reversed` (drop the link).
  ESI has **no `expired` status**, so an `outstanding` contract past its `date_expired` is
  derived as `expired`. A live/accepted contract that cites an appraisal but fails
  `contract_matches` becomes **`mismatch`** — a loud "don't accept this / a wrong one was
  accepted" signal. Void contracts are taken at face value (no item fetch).
- **One best link per appraisal.** When several contracts cite one appraisal (e.g. a
  rejected first attempt then a fresh one), keep the most meaningful by priority
  `in_progress > completed > mismatch > void`, tiebreak newest `issued`. `reconcile_for_corp`
  upserts the desired links and prunes the corp's rows whose contract vanished.
- **Ordering.** History/detail order by a status CASE — **in_progress, then mismatch, then
  completed, then void, then no-contract** — newest first within each bucket.
- **403 ≠ token failure.** A missing contracts scope or in-game role 403s
  (`CorporationContractsForbidden`); the watcher logs and skips the corp **without** setting
  `last_refresh_failed_at` — it's a per-scope access issue, not a dead refresh token that
  would also break structure pricing and the roster (mirrors the ADR-0036 members-403
  nuance, #68). Token-bearing failures log `repr(exc)` only, never `exc_info` (#75).

## Consequences

- Managers see, per appraisal, whether a real matching contract exists — and a **Mismatch**
  warning when a contract is off, closing a quiet way to be underpaid/overpaid.
- The status table is **eventually consistent** (background poll, ~15 min) and fully
  reconciled each run; it derives entirely from ESI + the appraisal, so it can be rebuilt by
  re-running the job.
- Reconnect friction: corps that connected before this ship must reconnect once to grant the
  contracts scope (surfaced in the Config panel). The character also needs the in-game role
  to read corp contracts; absent it, contract tracking silently no-ops while everything else
  keeps working.
- One extra ESI fan-out per corp per cycle (a contracts list + an items fetch per *matched
  live* contract — typically few). Multi-instance scheduler caveat as in ADR-0010/0034.

## Alternatives considered / rejected

- **Mutating the appraisal with a status column:** violates immutability (ADR-0014) and
  conflates the write-once quote with mutable contract lifecycle. Rejected for the side table.
- **Match on `public_id` alone (no items/price/location check):** trusts the member's
  Description blindly — a typo or a deliberately wrong contract would show "In Progress"
  against the wrong goods. The exact-match validation is the point.
- **A member-facing "I made the contract" button / webhook:** needs member action and trusts
  self-report; the watcher is passive and authoritative (reads EVE's own contract state).
- **Per-request live contract fetch on page load:** an ESI round-trip on every Appraisals
  view, rate-limit exposure, and no token on a member's own request. The background job
  amortizes it and reuses the corp token.
- **A manual "refresh contracts now" button:** deferred (background-only for MVP) — see the
  follow-ups in the architecture doc.
