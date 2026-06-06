from httpx import ASGITransport, AsyncClient

from app.main import app
from app.middleware import CSRF_HEADER


def _client(**kwargs) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", **kwargs
    )


async def test_unsafe_request_without_header_rejected():
    async with _client() as http:
        resp = await http.post("/api/v1/auth/logout")
    assert resp.status_code == 403


async def test_unsafe_request_with_header_allowed():
    async with _client(headers={CSRF_HEADER: "1"}) as http:
        resp = await http.post("/api/v1/auth/logout")
    assert resp.status_code == 204


async def test_safe_request_does_not_require_header():
    async with _client() as http:
        resp = await http.get("/api/v1/health")
    assert resp.status_code == 200
