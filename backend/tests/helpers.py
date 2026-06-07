"""Shared test helpers: fake SSO/ESI clients and login plumbing. Not a test module
(no `test_` prefix) so pytest won't collect it."""

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.plugins.esi import CharacterInfo, CorporationInfo, get_esi_client
from app.plugins.sso import OAuthToken, VerifiedCharacter, get_sso_client

CHAR_ID = 12345
CORP_ID = 98000001
CHAR_NAME = "Boss"


class FakeSso:
    configured = True

    def build_authorize_url(self, *, state: str, code_challenge: str) -> str:
        return f"https://login.eveonline.com/v2/oauth/authorize?state={state}"

    async def exchange_code(self, code: str, code_verifier: str) -> OAuthToken:
        return OAuthToken(access_token="fake-access-token")

    async def verify_token(self, access_token: str) -> VerifiedCharacter:
        return VerifiedCharacter(character_id=CHAR_ID, name=CHAR_NAME)


class BaseEsi:
    ceo_id = 99999  # not the logged-in char → member

    async def get_character(self, character_id: int) -> CharacterInfo:
        return CharacterInfo(name=CHAR_NAME, corporation_id=CORP_ID)

    async def get_character_corporation(self, character_id: int) -> int:
        return CORP_ID

    async def get_corporation(self, corporation_id: int) -> CorporationInfo:
        return CorporationInfo(name="Test Corp", ceo_id=self.ceo_id, ticker="T")

    async def get_character_roles(self, character_id: int, access_token: str) -> list[str]:
        return []


class CeoEsi(BaseEsi):
    ceo_id = CHAR_ID  # logged-in char is the CEO → can register, manager+


class MemberEsi(BaseEsi):
    pass


def make_client(esi: BaseEsi) -> AsyncClient:
    app.dependency_overrides[get_sso_client] = lambda: FakeSso()
    app.dependency_overrides[get_esi_client] = lambda: esi
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Buyback-CSRF": "1"},
    )


async def login(http: AsyncClient) -> dict:
    state = (await http.post("/api/v1/auth/login")).json()["state"]
    resp = await http.post(
        "/api/v1/auth/session", json={"code": "auth-code", "state": state}
    )
    assert resp.status_code == 200
    return resp.json()
