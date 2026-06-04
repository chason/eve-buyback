# 0001. REST backend on FastAPI + Pydantic

- **Status:** Accepted
- **Date:** 2026-06-04

## Context

The backend serves a REST/JSON API to a TypeScript SPA and may later serve other
consumers. We want async I/O (ESI/Fuzzwork are network-bound), strong request/
response validation, and a machine-readable API contract for generating a typed
frontend client. The team has chosen Python.

## Decision

Build the API with **FastAPI** and **Pydantic v2**. Use `async def` handlers
throughout so outbound HTTP (httpx) to ESI/Fuzzwork doesn't block. Lean on
FastAPI's dependency-injection for auth/role guards and its auto-generated OpenAPI
schema as the source of truth for the API contract.

## Consequences

- Free, accurate OpenAPI/Swagger → drives TS type generation ([ADR-0011](0011-api-contract-and-typescript-types.md)).
- Pydantic DTOs give validation + serialization in one place; keep them separate
  from SQLAlchemy ORM models.
- Async stack pairs with async SQLAlchemy ([ADR-0002](0002-sqlite-sqlalchemy-postgres-ready.md))
  and httpx (per the `eve-esi` skill).
- Requires an ASGI server (uvicorn) in deployment.

## Alternatives considered

- **Flask / Django REST Framework** — mature, but no first-class async or built-in
  OpenAPI-from-types; more boilerplate to reach the same contract.
- **Litestar** — comparable feature set, smaller ecosystem and community.
