# application/ — use cases

Conventions for this layer (one function per user action; orchestration only).

- **Orchestrate, don't implement.** A use case calls `plugins/` (outside APIs),
  `data/` repositories, and `domain/` pure functions. It contains **no SQL** and **no
  HTTP** — those live in `data/` and `interface/` respectively.
- **Own the unit of work.** Call `session.commit()` here, not in repositories, so a
  multi-step action commits atomically. **One documented exception:** the read-through
  market cache (`market.persist_market_rows`) commits its price-cache write in its *own*
  independent UoW, even when `create_appraisal` calls it mid-flight before its own
  commit. The cache is shared infrastructure (not part of the appraisal), the upsert is
  idempotent, and committing per-fetch lets the background refresh persist per hub — see
  the `persist_market_rows` docstring (#21).
- **Raise typed errors, not HTTP.** On a rule violation raise a class from `errors.py`
  — never `fastapi.HTTPException`. The interface maps error types to status codes in
  `interface/errors.py`. Adding a new error means: add the class here **and** register
  its status in `interface/errors.py._STATUS` (unmapped → 400).
- **Return Pydantic, not ORM or API DTOs.** Return `data/` records or application
  models (e.g. `AuthenticatedUser`, `SessionIdentity`). The interface maps results to
  the API DTOs in `schemas/` — don't import `schemas/` here.
- **Application models live here.** `SessionIdentity` (cookie payload) and
  `AuthenticatedUser` (resolved principal) are defined in `auth.py` because use cases
  produce/consume them; the interface only serializes/maps them.
- **Dependency direction.** May import `domain/`, `data/`, `plugins/`. Must **not**
  import `interface/` (the dependency points the other way).
