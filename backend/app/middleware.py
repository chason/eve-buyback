from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

# Custom header the SPA must send on state-changing API requests. A cross-origin
# attacker cannot set a custom header without a CORS preflight, which we never
# grant — so requiring it blocks forged cross-site requests as defense-in-depth
# on top of the SameSite=lax session cookie (ADR-0017).
CSRF_HEADER = "X-Buyback-CSRF"

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_PROTECTED_PREFIX = "/api/"


class CsrfHeaderMiddleware(BaseHTTPMiddleware):
    """Reject unsafe API requests that lack the CSRF header (ADR-0017)."""

    def __init__(self, app: ASGIApp, header_name: str = CSRF_HEADER) -> None:
        super().__init__(app)
        self._header_name = header_name

    async def dispatch(self, request: Request, call_next) -> Response:
        if (
            request.method in _UNSAFE_METHODS
            and request.url.path.startswith(_PROTECTED_PREFIX)
            and not request.headers.get(self._header_name)
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": f"Missing required {self._header_name} header"},
            )
        return await call_next(request)
