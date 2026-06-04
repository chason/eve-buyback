from httpx import ASGITransport, AsyncClient

from app.main import app


async def test_health_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "database": "ok"}
