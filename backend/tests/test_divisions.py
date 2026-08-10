"""ADR-0048: corp division names for the Stock page's wallet/hangar pickers — the
ESI read (named-only mapping, 403 degrade), the best-effort use case, and the API."""

import httpx

from app.application import divisions as divisions_app
from app.config import get_settings
from app.data.db import SessionLocal
from app.data.repositories import characters as characters_repo
from app.data.repositories import corp_esi_token as tokens_repo
from app.data.repositories import corporations as corporations_repo
from app.plugins.esi import CorporationDivisions, EsiClient
from app.plugins.token_cipher import TokenCipher
from tests.helpers import CHAR_ID, CORP_ID, CeoEsi, MemberEsi, login, make_client

# What ESI returns: `name` is present only for divisions the corp renamed (the
# master wallet, division 1, can never be renamed).
_ESI_BODY = {
    "wallet": [
        {"division": 1},
        {"division": 2, "name": "Buyback ISK"},
        {"division": 3},
    ],
    "hangar": [
        {"division": 1, "name": "Deliveries"},
        {"division": 2},
    ],
}


# --- ESI plugin -------------------------------------------------------------------


async def test_divisions_keep_only_named_entries():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/corporations/98/divisions/"
        return httpx.Response(200, json=_ESI_BODY)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        divisions = await EsiClient(http).get_corporation_divisions(98, "tok")

    assert divisions.wallet == {2: "Buyback ISK"}
    assert divisions.hangar == {1: "Deliveries"}


async def test_divisions_forbidden_degrades_to_empty():
    # 403 = missing scope (a pre-ADR-0048 grant) or a non-Director character. The
    # names are cosmetic, so the plugin degrades instead of raising.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Forbidden"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        divisions = await EsiClient(http).get_corporation_divisions(98, "tok")

    assert divisions == CorporationDivisions()


# --- use case + API -------------------------------------------------------------------


class _DivisionsEsi(CeoEsi):
    def __init__(self, divisions: CorporationDivisions) -> None:
        super().__init__()
        self._divisions = divisions

    async def get_corporation_divisions(self, corporation_id, access_token):
        return self._divisions


async def _seed_corp(*, with_token: bool):
    async with SessionLocal() as session:
        corp = await corporations_repo.create_corporation(
            session,
            eve_corporation_id=CORP_ID,
            name="Test Corp",
            ceo_character_id=CHAR_ID,
            registered_by_character_id=CHAR_ID,
        )
        if with_token:
            cipher = TokenCipher(get_settings().token_encryption_key)
            char = await characters_repo.upsert_character(
                session, eve_character_id=CHAR_ID, name="Boss"
            )
            await tokens_repo.upsert_token(
                session,
                corporation_id=corp.id,
                character_id=char.id,
                character_eve_id=CHAR_ID,
                character_name="Boss",
                encrypted_refresh_token=cipher.encrypt("refresh-tok"),
                scopes=get_settings().eve_corp_token_scopes,
            )
        await session.commit()


class _FakeSso:
    async def refresh_access_token(self, refresh_token):
        from app.plugins.sso import OAuthToken

        return OAuthToken(access_token="fresh", refresh_token=refresh_token)


async def test_use_case_degrades_to_empty_without_a_token():
    await _seed_corp(with_token=False)
    esi = _DivisionsEsi(CorporationDivisions(wallet={2: "Buyback ISK"}))
    async with SessionLocal() as session:
        names = await divisions_app.get_division_names(
            session, _FakeSso(), esi, corporation_eve_id=CORP_ID, cipher=None
        )
    assert names == CorporationDivisions()


async def test_api_returns_sorted_named_divisions():
    await _seed_corp(with_token=True)
    esi = _DivisionsEsi(
        CorporationDivisions(
            wallet={3: "Ore fund", 2: "Buyback ISK"}, hangar={1: "Deliveries"}
        )
    )
    async with make_client(esi) as http:
        await login(http)
        resp = await http.get("/api/v1/corporations/me/accounting/divisions")
    assert resp.status_code == 200
    assert resp.json() == {
        "wallet": [
            {"division": 2, "name": "Buyback ISK"},
            {"division": 3, "name": "Ore fund"},
        ],
        "hangar": [{"division": 1, "name": "Deliveries"}],
    }


async def test_api_returns_empty_lists_without_a_token():
    await _seed_corp(with_token=False)
    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.get("/api/v1/corporations/me/accounting/divisions")
    assert resp.status_code == 200
    assert resp.json() == {"wallet": [], "hangar": []}


async def test_api_is_manager_gated():
    await _seed_corp(with_token=False)
    async with make_client(MemberEsi()) as http:
        await login(http)
        resp = await http.get("/api/v1/corporations/me/accounting/divisions")
    assert resp.status_code == 403
