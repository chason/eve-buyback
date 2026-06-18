"""Shared test helpers: fake SSO/ESI clients and login plumbing. Not a test module
(no `test_` prefix) so pytest won't collect it."""

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.plugins.cache import MemoryCache, get_cache
from app.plugins.esi import CharacterInfo, CorporationInfo, get_esi_client
from app.plugins.esi_market import get_esi_market_client
from app.plugins.sso import OAuthToken, VerifiedCharacter, get_sso_client

CHAR_ID = 12345
CORP_ID = 98000001
CHAR_NAME = "Boss"


class FakeSso:
    configured = True

    def build_authorize_url(self, *, state: str, code_challenge: str) -> str:
        return f"https://login.eveonline.com/v2/oauth/authorize?state={state}"

    async def exchange_code(self, code: str, code_verifier: str) -> OAuthToken:
        # EVE returns a refresh token on the auth-code exchange; the login flow keeps it
        # encrypted in the session to power "Open in EVE" (ADR-0038).
        return OAuthToken(
            access_token="fake-access-token", refresh_token="fake-login-refresh"
        )

    async def verify_token(self, access_token: str) -> VerifiedCharacter:
        return VerifiedCharacter(character_id=CHAR_ID, name=CHAR_NAME)

    async def refresh_access_token(self, refresh_token: str) -> OAuthToken:
        return OAuthToken(access_token="fake-access-token", refresh_token=refresh_token)


class BaseEsi:
    ceo_id = 99999  # not the logged-in char → member

    def __init__(self) -> None:
        # Records ("Open in EVE", ADR-0038) open-window calls for endpoint assertions.
        self.opened_contracts: list[tuple[int, str]] = []

    async def open_contract_window(self, contract_id: int, access_token: str) -> None:
        self.opened_contracts.append((contract_id, access_token))

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


class FakeEsiMarket:
    """Stand-in for the ESI market client. The default Jita hub is a Fuzzwork hub, so
    pricing/resolution never touch this; a test that selects a non-Fuzzwork station
    gets a canned region/name resolution."""

    def __init__(self) -> None:
        self.region_calls = 0

    async def resolve_station(self, station_id: int) -> tuple[int, str]:
        return 10000002, f"Station {station_id}"

    async def get_region_aggregates(self, *, region_id, station_id, type_ids):
        self.region_calls += 1
        return {}

    async def get_structure_aggregates(self, *, structure_id, type_ids, access_token):
        return {}

    async def get_all_structure_aggregates(self, *, structure_id, access_token):
        return {}


def make_client(esi: BaseEsi) -> AsyncClient:
    app.dependency_overrides[get_sso_client] = lambda: FakeSso()
    app.dependency_overrides[get_esi_client] = lambda: esi
    app.dependency_overrides[get_esi_market_client] = lambda: FakeEsiMarket()
    # The ASGI transport doesn't run the lifespan, so app.state.cache is unset. Close
    # over ONE cache so it persists across requests like the production singleton
    # (get_cache returns the process-wide app.state.cache), not a fresh one per call.
    cache = MemoryCache()
    app.dependency_overrides[get_cache] = lambda: cache
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
