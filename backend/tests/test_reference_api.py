from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.data.db import SessionLocal
from app.data.repositories import sde as sde_repo
from app.main import app
from app.plugins.esi import CharacterInfo, CorporationInfo, get_esi_client
from app.plugins.sso import OAuthToken, VerifiedCharacter, get_sso_client


class FakeSso:
    configured = True

    def build_authorize_url(self, *, state: str, code_challenge: str) -> str:
        return f"https://login.eveonline.com/v2/oauth/authorize?state={state}"

    async def exchange_code(self, code: str, code_verifier: str) -> OAuthToken:
        return OAuthToken(access_token="fake-access-token")

    async def verify_token(self, access_token: str) -> VerifiedCharacter:
        return VerifiedCharacter(character_id=12345, name="Test Pilot")


class FakeEsi:
    async def get_character(self, character_id: int) -> CharacterInfo:
        return CharacterInfo(name="Test Pilot", corporation_id=98000001)

    async def get_character_corporation(self, character_id: int) -> int:
        return 98000001

    async def get_corporation(self, corporation_id: int) -> CorporationInfo:
        return CorporationInfo(name="Test Corp", ceo_id=99999, ticker="T")

    async def get_character_roles(self, character_id: int, access_token: str) -> list[str]:
        return []


@pytest.fixture
def client():
    app.dependency_overrides[get_sso_client] = lambda: FakeSso()
    app.dependency_overrides[get_esi_client] = lambda: FakeEsi()
    yield AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Buyback-CSRF": "1"},
    )
    app.dependency_overrides.clear()


async def _seed() -> None:
    async with SessionLocal() as session:
        await sde_repo.bulk_upsert_market_groups(
            session,
            [
                {"market_group_id": 1, "parent_id": None, "name": "Ore"},
                {"market_group_id": 2, "parent_id": 1, "name": "Moon Ores"},
            ],
        )
        await sde_repo.bulk_upsert_types(
            session,
            [
                {"type_id": 34, "name": "Tritanium", "group_id": 18,
                 "market_group_id": 1, "volume": Decimal("0.01"), "published": True},
                {"type_id": 35, "name": "Pyerite", "group_id": 18,
                 "market_group_id": 1, "volume": Decimal("0.01"), "published": True},
            ],
        )
        await session.commit()


async def _login(http: AsyncClient) -> None:
    state = (await http.post("/api/v1/auth/login")).json()["state"]
    resp = await http.post(
        "/api/v1/auth/session", json={"code": "auth-code", "state": state}
    )
    assert resp.status_code == 200


async def test_search_types_requires_auth(client):
    async with client as http:
        resp = await http.get("/api/v1/types/search?q=trit")
    assert resp.status_code == 401


async def test_search_types(client):
    await _seed()
    async with client as http:
        await _login(http)
        resp = await http.get("/api/v1/types/search?q=trit")
    assert resp.status_code == 200
    body = resp.json()
    assert [t["name"] for t in body] == ["Tritanium"]
    assert body[0]["type_id"] == 34
    assert body[0]["market_group_id"] == 1


async def test_search_types_rejects_short_query(client):
    async with client as http:
        await _login(http)
        resp = await http.get("/api/v1/types/search?q=t")
    assert resp.status_code == 422  # min_length=2


async def test_list_market_groups(client):
    await _seed()
    async with client as http:
        await _login(http)
        resp = await http.get("/api/v1/market-groups")
    assert resp.status_code == 200
    body = resp.json()
    assert {g["market_group_id"] for g in body} == {1, 2}
    moon = next(g for g in body if g["market_group_id"] == 2)
    assert moon["parent_id"] == 1
