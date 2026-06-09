"""Serve the built single-page app in production (ADR-0012).

In production the FastAPI app serves the compiled SPA static assets alongside
``/api/v1`` so the whole thing is one deployable on one origin. Client-side
routes (e.g. ``/appraisals/abc``) have no file on disk, so a plain static mount
would 404 them; this subclass falls back to ``index.html`` for those, letting
React Router take over on the client.

The fallback applies only to **extension-less** paths (the shape of a client
route). A request that looks like a file — ``/assets/index-abcd.js`` — that
isn't on disk still returns a real 404, so a stale/bogus asset URL fails
cleanly instead of serving HTML with a 200 (which browsers reject with a
misleading MIME error).
"""

from pathlib import PurePosixPath

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class SpaStaticFiles(StaticFiles):
    """StaticFiles that serves index.html for unmatched (client-side) routes."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            # Only history-fallback for route-shaped paths; let missing files 404.
            if exc.status_code == 404 and not PurePosixPath(path).suffix:
                return await super().get_response("index.html", scope)
            raise
