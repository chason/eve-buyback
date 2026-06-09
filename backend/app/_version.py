"""Single source of truth for the application version.

Versioning is intentionally simple: **each merged PR bumps this by one** (see the
note in CLAUDE.md). It's served at `GET /api/v1/version` and shown in the UI footer,
and is the FastAPI app version (so it appears in the OpenAPI doc too).
"""

APP_VERSION = "10"
