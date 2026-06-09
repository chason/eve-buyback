from httpx import ASGITransport, AsyncClient

from app._version import APP_VERSION
from app.main import app


async def test_health_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "database": "ok"}


async def test_version_reports_app_version():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/version")

    assert resp.status_code == 200
    assert resp.json() == {"version": APP_VERSION}
