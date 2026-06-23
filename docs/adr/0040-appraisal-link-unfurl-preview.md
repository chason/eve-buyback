# 0040. Server-rendered link-unfurl preview for shared appraisals

- **Status:** Accepted
- **Date:** 2026-06-23
- **Relates to:** [ADR-0012](0012-single-deployable-packaging.md) (backend serves the
  SPA), [ADR-0014](0014-persisted-appraisals.md) (appraisals + their random `public_id`
  share handle), [ADR-0030](0030-buyback-drop-off-locations.md) (the drop-off location
  shown), [ADR-0020](0020-decimal-money-values.md) (Decimal money), [ADR-0003](0003-multi-tenant-corp-scoping.md)
  (corp scoping — deliberately bypassed here)

## Context

Members share an appraisal link (`/a/{public_id}`) in Discord/Slack to coordinate a
buyback. Those apps **unfurl** a link by fetching it and reading Open Graph `<meta>` tags
from the HTML `<head>` — their crawlers **do not execute JavaScript**. The app is a Vite
SPA, so every shared link unfurls with the same generic `index.html` card; the preview
says nothing about the appraisal.

We want the unfurl to show the appraisal's **value** and **drop-off location** so a
manager can eyeball a shared link without opening it.

Two constraints shape the design:

- The unfurl request is **unauthenticated** — a crawler has no session. So the data in
  the preview must be served without login.
- The appraisal read API (`GET /api/v1/appraisals/{public_id}`) requires auth and is
  corp-scoped. It can't serve the crawler.

## Decision

**Add a server-rendered route `GET /a/{public_id}` that injects Open Graph `<meta>` tags
for the appraisal into the SPA's `index.html`, served publicly (unauthenticated). It
exposes only the appraisal's total value and drop-off location — nothing else.**

- **Intercept before the SPA mount.** The route is registered ahead of the `/` static
  mount (ADR-0012), so it handles `/a/{public_id}`: it reads the built `index.html`,
  injects the tags before `</head>`, and returns the whole shell — a human's browser still
  hydrates the real React page; the crawler reads the tags. `index.html` ships no OG tags,
  so injection can't create duplicates. When there's no build on disk (dev, where Vite
  serves the SPA; or tests) a minimal HTML shell carrying just the tags is returned.
- **A public, un-scoped read of two fields.** A dedicated `public_preview` repository
  query returns **only** `accepted_total` + `delivery_location_name`, keyed by the
  `public_id` and **not** corp-scoped — the unguessable id (`secrets.token_urlsafe(9)`,
  ~72 bits) is the capability. This is the first intentionally cross-corp, unauthenticated
  read; it's narrow by construction (two non-PII fields, no character, no items, no line
  data) and lives behind its own use case so the exposure is auditable in one place.
- **Minimal, non-PII copy.** Title is the value (`1,230,000 ISK · Buyback appraisal`),
  description names the drop-off location when present. **No character name** — even
  though we know the creator — because the preview is world-readable to anyone holding the
  link; keeping it to value + location bounds what a leaked link reveals. ISK is formatted
  to whole ISK (banker's rounding, ADR-0020). Injected values are HTML-escaped.
- **Unknown id → generic card, 200.** A non-resolving id returns the SPA shell with the
  default site card (the client router renders its own 404), so the route never reveals
  whether an id exists.

## Consequences

- Shared appraisal links unfurl in Discord/Slack/Twitter with the value + location.
- **A shared link now exposes the appraisal's total value and drop-off location to anyone
  who has it, without logging in** — including link-unfurl services, which may cache it.
  This is disclosed on the Privacy / Data-Use page. The full itemized appraisal still
  requires auth + corp membership.
- The exposure is intentionally limited to two fields. Widening it (character, items,
  image) is a deliberate future decision, not an accident of the current code.
- One extra cached file read of `index.html` per preview request; negligible.

## Alternatives considered / rejected

- **Static OG tags in `index.html`:** can't carry per-appraisal data — the crawler reads
  one fixed file. Defeats the purpose.
- **Client-side `<meta>` injection (React Helmet etc.):** crawlers don't run JS, so they'd
  never see it. Non-starter for unfurls.
- **Reuse the authed appraisal endpoint:** the crawler has no session; it would just get a
  401 and the generic card.
- **Include the contracting character / a portrait image:** declined for now — it puts a
  character's name (PII) into a world-readable preview behind only the link. Kept to value
  + location; revisit if the trade-off changes (would require updating Privacy again).
- **Crawler-only rendering (sniff User-Agent):** the data still leaves to the crawler, UA
  is trivially spoofed, and it complicates the route for no real privacy gain. Serve the
  same shell to everyone.
