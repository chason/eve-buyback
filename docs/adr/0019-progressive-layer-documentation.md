# 0019. Progressive documentation with layer-local CLAUDE.md

- **Status:** Accepted
- **Date:** 2026-06-07
- **Relates to:** [0018](0018-layered-backend-architecture.md) (the layers being documented)

## Context

The layered backend ([ADR-0018](0018-layered-backend-architecture.md)) has
per-layer conventions that are easy to violate and not visible from the code alone —
e.g. "repositories return Pydantic records, never ORM" or "use cases own the
`commit()`, never raise `HTTPException`." We want that guidance in front of whoever
(human or agent) edits a layer.

Claude Code loads context files differently by location: the **root `CLAUDE.md`** is
loaded into **every** session, while a **subdirectory `CLAUDE.md`** is pulled in
**on demand** when files in that directory are read or edited. So root space is
precious (it taxes every session), whereas nested files cost context only when
relevant.

## Decision

Use a **hybrid**: the root `CLAUDE.md` holds the cross-cutting map and rules; a layer
**may** carry its own `CLAUDE.md` with its local conventions, which loads
just-in-time when that layer is touched.

Guidelines:

- **Document invariants and the "why", not file inventories.** A per-directory list of
  files duplicates `ls` and rots the moment a file is added; conventions and rules age
  far more slowly.
- **Add a layer file only when it earns its keep** — when a layer has non-obvious local
  rules. Start with the layers that have the most hidden knowledge.
- The root file notes which layers carry their own `CLAUDE.md`.

Initial coverage: `app/data/` and `app/application/` (highest hidden-rule density).
`interface/`, `domain/`, and `plugins/` are deferred until they accumulate enough
local convention (e.g. when M4 adds a Fuzzwork gateway and pricing use cases).

## Consequences

- Layer detail loads only when working in that layer, keeping the always-on root
  context lean.
- Guidance lives next to the code it governs, reducing the chance it's missed.
- Drift risk is mitigated by documenting invariants (not inventories) and only where
  it pays; the cost is keeping a layer's file current when its conventions change.

## Alternatives considered

- **Everything in the root `CLAUDE.md`** — one file to maintain, but it bloats the
  always-loaded context with detail irrelevant to most sessions; rejected.
- **A `CLAUDE.md` in every directory** — uniform, but tiny/obvious directories get
  noise that drifts; rejected in favor of adding them only where warranted.
- **An external docs site / wiki** — richer formatting, but it is not pulled into the
  agent's context automatically while editing; rejected for this purpose.
