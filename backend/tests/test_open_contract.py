"""Open-in-EVE (ADR-0038): the use case that refreshes the session-held login token and
calls ESI open-window for an appraisal's matched contract, plus the endpoint wiring and the
`can_open_contract` session flag."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from cryptography.fernet import Fernet

from app.application import open_contract as open_contract_app
from app.application.errors import NoMatchedContract, OpenContractUnavailable
from app.data.db import SessionLocal
from app.data.models import AppraisalContract
from app.data.repositories import appraisal_contracts as links_repo
from app.data.repositories import appraisals as appraisals_repo
from app.data.repositories import corporations as corporations_repo
from app.plugins.esi import OpenWindowForbidden
from app.plugins.sso import OAuthToken
from app.plugins.token_cipher import TokenCipher
from tests.helpers import CORP_ID, CeoEsi, login, make_client

CIPHER = TokenCipher(Fernet.generate_key().decode())
ENC_TOKEN = CIPHER.encrypt("login-refresh").decode()
NOW = datetime(2026, 6, 19, 12, 0, 0, tzinfo=UTC)
CONTRACT_ID = 42


class FakeSso:
    def __init__(self, *, rotated: str | None = None, fail: bool = False):
        self.rotated = rotated
        self.fail = fail
        self.refreshed: list[str] = []

    async def refresh_access_token(self, refresh_token: str) -> OAuthToken:
        self.refreshed.append(refresh_token)
        if self.fail:
            req = httpx.Request("POST", "https://login.eveonline.com/v2/oauth/token")
            raise httpx.HTTPStatusError(
                "invalid_grant", request=req, response=httpx.Response(400, request=req)
            )
        return OAuthToken(
            access_token="fresh-access", refresh_token=self.rotated or refresh_token
        )


class FakeEsi:
    def __init__(self, *, forbid: bool = False):
        self.forbid = forbid
        self.calls: list[tuple[int, str]] = []

    async def open_contract_window(self, contract_id: int, access_token: str) -> None:
        self.calls.append((contract_id, access_token))
        if self.forbid:
            raise OpenWindowForbidden()


async def _seed(
    public_id: str, *, corp_eve: int = 98000777, with_link: bool = True
) -> uuid.UUID:
    async with SessionLocal() as s:
        corp = await corporations_repo.create_corporation(
            s, eve_corporation_id=corp_eve, name="Corp",
            ceo_character_id=corp_eve + 1, registered_by_character_id=corp_eve + 1,
        )
        await s.commit()
        corp_uuid = corp.id
    async with SessionLocal() as s:
        await appraisals_repo.create_appraisal(
            s, public_id=public_id, corporation_id=corp_uuid,
            created_by_character_id=corp_eve + 1, market_hub_id="60003760",
            accepted_total=Decimal("1.00"), rejected_count=0,
            request_json={"items": []}, lines=[],
        )
        await s.commit()
    if with_link:
        async with SessionLocal() as s:
            id_map = await links_repo.appraisal_public_id_to_id(
                s, corporation_id=corp_uuid
            )
            s.add(
                AppraisalContract(
                    appraisal_id=id_map[public_id], corporation_id=corp_uuid,
                    contract_id=CONTRACT_ID, status="in_progress", issued_at=NOW,
                )
            )
            await s.commit()
    return corp_uuid


async def _run(esi, sso, *, public_id, token=ENC_TOKEN, corp_eve=98000777):
    async with SessionLocal() as s:
        return await open_contract_app.open_matched_contract(
            s, sso, esi, CIPHER,
            corporation_id=corp_eve, public_id=public_id,
            encrypted_login_token=token,
        )


# --- use case ---


async def test_opens_the_matched_contract_and_returns_rotated_token():
    await _seed("openHAPPYaaa")
    esi, sso = FakeEsi(), FakeSso(rotated="rotated-refresh")

    new_token = await _run(esi, sso, public_id="openHAPPYaaa")

    assert esi.calls == [(CONTRACT_ID, "fresh-access")]  # opened with the fresh access tok
    assert sso.refreshed == ["login-refresh"]  # decrypted the session token to refresh
    assert CIPHER.decrypt(new_token.encode()) == "rotated-refresh"  # re-seal the new one


async def test_no_matched_contract_raises():
    await _seed("openNOLINKaa", with_link=False)
    with pytest.raises(NoMatchedContract):
        await _run(FakeEsi(), FakeSso(), public_id="openNOLINKaa")


async def test_corp_scoped_other_corp_cannot_open():
    await _seed("openCORPAaaa", corp_eve=98000111)
    # A different (registered) corp asking for that appraisal sees no matched contract.
    await _seed("openCORPBaaa", corp_eve=98000222, with_link=False)
    with pytest.raises(NoMatchedContract):
        await _run(FakeEsi(), FakeSso(), public_id="openCORPAaaa", corp_eve=98000222)


async def test_no_session_token_is_unavailable():
    await _seed("openNOTOKaaa")
    esi = FakeEsi()
    with pytest.raises(OpenContractUnavailable):
        await _run(esi, FakeSso(), public_id="openNOTOKaaa", token=None)
    assert esi.calls == []  # never reached ESI


async def test_revoked_grant_is_unavailable():
    await _seed("openREVOKEaa")
    with pytest.raises(OpenContractUnavailable):
        await _run(FakeEsi(), FakeSso(fail=True), public_id="openREVOKEaa")


async def test_missing_scope_is_unavailable():
    await _seed("openSCOPEaaa")
    with pytest.raises(OpenContractUnavailable):
        await _run(FakeEsi(forbid=True), FakeSso(), public_id="openSCOPEaaa")


# --- endpoint + session flag ---


async def test_login_session_can_open_contract():
    # The fake SSO returns a refresh token, so a fresh login keeps it (ADR-0038).
    async with make_client(CeoEsi()) as http:
        me = await login(http)
    assert me["can_open_contract"] is True


async def test_endpoint_opens_contract_and_204s():
    # Seed this CORP_ID corp + a linked appraisal, then drive the endpoint via HTTP.
    async with SessionLocal() as s:
        corp = await corporations_repo.create_corporation(
            s, eve_corporation_id=CORP_ID, name="Test Corp",
            ceo_character_id=99999, registered_by_character_id=99999,
        )
        await s.commit()
        corp_uuid = corp.id
    async with SessionLocal() as s:
        await appraisals_repo.create_appraisal(
            s, public_id="endpointAaaa", corporation_id=corp_uuid,
            created_by_character_id=99999, market_hub_id="60003760",
            accepted_total=Decimal("1.00"), rejected_count=0,
            request_json={"items": []}, lines=[],
        )
        await s.commit()
    async with SessionLocal() as s:
        id_map = await links_repo.appraisal_public_id_to_id(
            s, corporation_id=corp_uuid
        )
        s.add(
            AppraisalContract(
                appraisal_id=id_map["endpointAaaa"], corporation_id=corp_uuid,
                contract_id=99, status="completed", issued_at=NOW,
            )
        )
        await s.commit()

    esi = CeoEsi()
    async with make_client(esi) as http:
        await login(http)
        resp = await http.post("/api/v1/appraisals/endpointAaaa/open-contract")

    assert resp.status_code == 204
    assert esi.opened_contracts == [(99, "fake-access-token")]


async def test_endpoint_404_when_no_matched_contract():
    async with SessionLocal() as s:
        corp = await corporations_repo.create_corporation(
            s, eve_corporation_id=CORP_ID, name="Test Corp",
            ceo_character_id=99999, registered_by_character_id=99999,
        )
        await s.commit()
        corp_uuid = corp.id
    async with SessionLocal() as s:
        await appraisals_repo.create_appraisal(
            s, public_id="endpointNOco", corporation_id=corp_uuid,
            created_by_character_id=99999, market_hub_id="60003760",
            accepted_total=Decimal("1.00"), rejected_count=0,
            request_json={"items": []}, lines=[],
        )
        await s.commit()

    async with make_client(CeoEsi()) as http:
        await login(http)
        resp = await http.post("/api/v1/appraisals/endpointNOco/open-contract")
    assert resp.status_code == 404
