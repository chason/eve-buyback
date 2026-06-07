# data/ — database logic

Conventions for this layer (the only place that talks to the database).

- **Two kinds of files, kept separate.** `models/` holds SQLAlchemy ORM entities;
  `repositories/` holds query/write functions. Don't put queries in model files.
- **Repositories never return ORM entities.** They return the Pydantic read-models in
  `records.py`. If you need to return a new shape, add a record there. Records with
  `from_attributes=True` can be built with `Record.model_validate(orm_obj)`;
  join-derived fields (e.g. `ManagerRecord.character_name`) are passed in explicitly.
- **Keys (ADR-0025).** App-owned entities have a **UUID primary key** (`id`); the EVE
  natural id is a unique column named `eve_id`, and internal FK columns hold the
  referenced row's **UUID**. Repositories look up by `eve_id`, store/join UUIDs, and
  resolve EVE-id ↔ UUID at this layer so use cases thread the UUID. Records that map to
  an EVE-id DTO field expose it via a Pydantic `validation_alias="eve_id"` so the API
  contract stays EVE-keyed. SDE reference tables + `MarketPrice`/`SdeMetadata` cache are
  EVE-keyed by nature and keep their EVE-id PKs; `PricingRule.target_id` is an EVE id.
- **Repositories do not `commit()`.** They may `flush()` + `refresh()` to populate
  server defaults (e.g. `registered_at`), but the unit of work — the `commit()` — is
  owned by the **application** layer. This keeps multi-step use cases atomic.
- **Session is passed in, not imported.** Every repository function takes the
  `AsyncSession` as its first argument (injected at the interface boundary, threaded
  down through use cases). `get_session()` lives in `db.py` for the interface to use.
- **Dependency direction.** This layer may use `domain/` types but must **not** import
  from `application/`, `interface/`, `plugins/`, or `schemas/`.
- **Adding a model:** define it in `models/<entity>.py` (import `Base` from
  `data.db`), register it in `models/__init__.py` so Alembic and `Base.metadata` see
  it, then autogenerate a migration (`uv run alembic revision --autogenerate`).
