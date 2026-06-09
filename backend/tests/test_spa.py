"""SPA static serving + history fallback (ADR-0012, interface/spa.py)."""

from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.interface.spa import SpaStaticFiles

INDEX_MARKER = "<!doctype html><title>spa</title><div id=root></div>"


def _build_app(static_dir: Path) -> FastAPI:
    """Mirror main._mount_spa: API first, SPA mounted under '/' last."""
    (static_dir / "index.html").write_text(INDEX_MARKER, encoding="utf-8")
    assets = static_dir / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('hi')", encoding="utf-8")

    app = FastAPI()

    @app.get("/api/v1/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "ok"}

    app.mount("/", SpaStaticFiles(directory=static_dir, html=True), name="spa")
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_root_serves_index(tmp_path: Path):
    async with _client(_build_app(tmp_path)) as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert INDEX_MARKER in resp.text


async def test_client_route_falls_back_to_index(tmp_path: Path):
    """An extension-less client route has no file on disk → serve index.html."""
    async with _client(_build_app(tmp_path)) as client:
        resp = await client.get("/appraisals/abc123")
    assert resp.status_code == 200
    assert INDEX_MARKER in resp.text


async def test_api_route_takes_priority_over_spa(tmp_path: Path):
    async with _client(_build_app(tmp_path)) as client:
        resp = await client.get("/api/v1/ping")
    assert resp.status_code == 200
    assert resp.json() == {"pong": "ok"}


async def test_real_asset_is_served(tmp_path: Path):
    async with _client(_build_app(tmp_path)) as client:
        resp = await client.get("/assets/app.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


async def test_missing_asset_404s_not_index(tmp_path: Path):
    """A file-shaped path that doesn't exist must 404, not serve HTML."""
    async with _client(_build_app(tmp_path)) as client:
        resp = await client.get("/assets/missing-xyz.js")
    assert resp.status_code == 404
    assert INDEX_MARKER not in resp.text
