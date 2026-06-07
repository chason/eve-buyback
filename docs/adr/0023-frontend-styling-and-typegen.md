# 0023. Frontend styling (Pico.css) and OpenAPI type generation

- **Status:** Accepted
- **Date:** 2026-06-07
- **Relates to:** [0011](0011-api-contract-and-typescript-types.md) (OpenAPI types), [0013](0013-frontend-stack.md) (frontend stack)

## Context

[ADR-0013](0013-frontend-stack.md) chose React + Vite + TanStack Query and explicitly
left the **UI component library "deferred / open"**. [ADR-0011](0011-api-contract-and-typescript-types.md)
chose **`openapi-typescript`** for derived types but the workflow was never wired up.
Building the SPA (M6) forces both decisions.

## Decision

**Styling: Pico.css** (`@picocss/pico`), a small classless CSS framework, plus a tiny
`src/index.css` for app-specific tweaks. It gives a clean, themeable default look with
almost no markup overhead and a negligible dependency — consistent with the project's
"small dependency surface" value. This resolves ADR-0013's open question for the MVP;
a full component library can still be adopted later without disturbing data access.

**Type generation: a committed schema, generated offline.** The backend exports its
OpenAPI document to `frontend/openapi.json` via `python -m app.openapi_export` (uses
`app.openapi()` — no running server needed). The frontend's `npm run gen:api`
(`openapi-typescript openapi.json -o src/api/schema.d.ts`) turns it into types, checked
in per ADR-0011. `src/api/types.ts` re-exports friendly aliases
(`components["schemas"]["AppraisalOut"]` → `AppraisalOut`), and the hand-written
interfaces in `api/*.ts` were replaced with these. Generating from a **committed file**
(rather than a live URL) keeps it reproducible in CI.

**Frontend quality gate.** ESLint (flat config) + Vitest + React Testing Library, run
in the pre-commit hook alongside `tsc` — mirroring the backend's ruff + pytest.

## Consequences

- One source of truth: a backend DTO change → re-run `app.openapi_export` + `gen:api`
  → TypeScript compile errors surface drift. The generated `schema.d.ts` is lint-ignored
  and never edited by hand.
- Money stays a **string** end-to-end: the generated types render Decimal fields as
  `string`, and `lib/format.ts` formats without `Number()` (ADR-0020).
- Pico styles via semantic HTML; we add classes sparingly (`.isk`, `.num`, `.rejected`).
- A trivial endpoint without a response model (`/health` → `dict[str, str]`) has no named
  schema, so its type is hand-written — the rare, acceptable exception.

## Alternatives considered

- **Tailwind / a component library (Mantine, Chakra)** — more power, but heavier deps and
  (for Tailwind) verbose markup; overkill for an MVP whose UI is forms, tables, and a
  search box. Pico covers it with one small dependency.
- **Hand-rolled CSS modules** — full control, zero deps, but more effort to make things
  look decent; not worth it at this stage.
- **Generating types from a live `/openapi.json` URL** — the common dev workflow, but
  requires a running backend and isn't reproducible in CI; the committed-file approach is
  strictly more robust.
- **A generated client SDK** (vs types + the `fetch` wrapper) — rejected already by
  ADR-0011; unchanged.
