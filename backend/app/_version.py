"""Single source of truth for the application version.

Versioning is intentionally simple: **each merged PR is one version**, and the bump
happens **automatically on merge** — `.github/workflows/version-bump.yml` commits
the increment to main (ADR-0032). Do NOT bump this in a PR: with parallel PRs open,
both would claim the same number. It's served at `GET /api/v1/version`, shown in
the UI top bar, and is the FastAPI app version (so it appears in the OpenAPI doc).
"""

APP_VERSION = "40"
