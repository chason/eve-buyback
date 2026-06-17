# 0014. Persisted, immutable appraisals with shareable ids

- **Status:** Accepted
- **Date:** 2026-06-04

## Context

A quote is not just a throwaway calculation: members and managers need to **refer
back to a specific appraisal** — to show "this is what I was quoted", to reconcile
a payout, or to audit what the program offered at a point in time. Because market
prices and pricing rules change, a quote computed today must remain reproducible
tomorrow regardless of later changes.

## Decision

Persist every submitted appraisal as a **first-class, immutable resource**.
Creating an appraisal (`POST /api/v1/appraisals`) computes the result and **stores
a full snapshot**, then returns it with a stable identifier; it is never
recomputed on read.

Each `Appraisal` stores:

- `id` (internal PK) and a **`public_id`** — a random, URL-safe, non-sequential
  slug used in links and as the reference handle.
- `corp_id`, `created_by` (character), `created_at`, `market_hub_id`.
- Header totals: `accepted_total` (and rejected count).
- **Per-line snapshot** (JSON or child rows): `type_id`, `quantity`, the
  **resolved `basis` and `percentage`**, the **market unit value used**, computed
  `unit_price`, `line_total`, `status` (`accepted`/`rejected`), and `reason`.

Snapshots are **write-once**: appraisals are not edited or recomputed. Later
changes to rules ([ADR-0007](0007-pricing-rule-taxonomy.md)) or market prices
([ADR-0006](0006-market-data-fuzzwork.md)) do not alter past appraisals.

**Visibility** is corp-scoped ([ADR-0003](0003-multi-tenant-corp-scoping.md)): any
member of the owning corp may fetch an appraisal by `public_id` (it doubles as a
within-corp share link); members list their own, managers/CEO list the corp's.
Cross-corp access is denied.

## Consequences

- Referenceability and auditability are guaranteed — a `public_id` always shows the
  exact numbers offered at creation time.
- Storage grows with usage; line items are small. Retain indefinitely for MVP;
  pruning/archival is a later concern (noted as a follow-up).
- The snapshot must capture *enough* to be self-explanatory without re-reading rules
  or prices — hence storing basis/percentage/unit-value per line, not just totals.
- The random `public_id` avoids enumeration and lets the same id back both "open my
  appraisal" and "share within corp" without a separate sharing system.
- Raises the core quote endpoint to a REST resource: `POST /appraisals` (create) and
  `GET /appraisals/{public_id}` (read), replacing an ephemeral `/quote`.
- **History hides zero-value appraisals** (#31): with no ephemeral preview (still
  deferred, below), a curiosity "what's this worth" click that prices to nothing is
  still saved and reachable by its `public_id`, but doesn't surface in the member's or
  corp's history list (the `list_*` queries filter `accepted_total > 0`). The submit
  control is labelled "Save appraisal" with a note that it creates a corp-visible
  record, so the persistence isn't a surprise.

## Alternatives considered

- **Compute-and-forget (no persistence)** — simplest, but provides nothing to
  reference and can't be reproduced after prices move; rejected per the requirement.
- **Persist and recompute on read** — would keep "live" numbers but breaks the whole
  point of a stable reference and audit trail.
- **Sequential/guessable ids** — trivial to enumerate across a corp; the random
  `public_id` is barely more work and much safer as a share handle.
- **Separate ephemeral "preview" vs saved appraisal** — useful later to avoid
  clutter while typing; deferred. MVP persists on submit.
