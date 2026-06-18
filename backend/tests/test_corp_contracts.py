"""Corp contract watcher (ADR-0037): the background use case that reads the corp's ESI
contracts via the stored Corp ESI token, matches each to an appraisal by its public_id,
validates items/price/location, and writes one best status per appraisal — plus the
list/detail ordering that surfaces those statuses."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from cryptography.fernet import Fernet

from app.application import corp_contracts as contracts_app
from app.application import corp_esi_token as corp_esi_token_app
from app.application.auth import AuthenticatedUser
from app.data.db import SessionLocal
from app.data.repositories import appraisal_contracts as links_repo
from app.data.repositories import appraisals as appraisals_repo
from app.data.repositories import corp_esi_token as tokens_repo
from app.data.repositories import corporations as corporations_repo
from app.domain.roles import Role
from app.plugins.esi import (
    CharacterInfo,
    ContractItem,
    CorporationContract,
    CorporationContractsForbidden,
    CorporationInfo,
)
from app.plugins.sso import OAuthToken, VerifiedCharacter
from app.plugins.token_cipher import TokenCipher

CORP_EVE_ID = 98000123
CHAR_ID = 4242
LOCATION = "1035000000001"
CIPHER = TokenCipher(Fernet.generate_key().decode())

NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
ISSUED = NOW - timedelta(hours=2)


def _user(role: Role = "ceo") -> AuthenticatedUser:
    return AuthenticatedUser(
        character_id=CHAR_ID,
        character_name="Boss",
        corporation_id=CORP_EVE_ID,
        corporation_name="Test Corp",
        role=role,
        is_director=False,
        corporation_registered=True,
    )


class FakeSso:
    configured = True

    def build_authorize_url(self, *, state, code_challenge, scopes=None):
        return f"https://login.eveonline.com/authorize?state={state}"

    async def exchange_code(self, code, code_verifier):
        return OAuthToken(access_token="access-initial", refresh_token="refresh-1")

    async def verify_token(self, access_token):
        return VerifiedCharacter(character_id=CHAR_ID, name="Boss")

    async def refresh_access_token(self, refresh_token):
        return OAuthToken(access_token="access-fresh", refresh_token=refresh_token)


class ContractsEsi:
    """ESI fake: corp-token validation methods (for connecting the token) plus the
    contract reads the watcher calls. Counts item fetches so a test can assert a voided
    contract is surfaced without one."""

    def __init__(self, *, contracts=None, items=None, forbid=False):
        self._contracts = contracts or []
        self._items = items or {}  # contract_id -> list[ContractItem]
        self.forbid = forbid
        self.item_fetches: list[int] = []

    # --- token-connect validation ---
    async def get_character(self, character_id):
        return CharacterInfo(name="Boss", corporation_id=CORP_EVE_ID)

    async def get_character_corporation(self, character_id):
        return CORP_EVE_ID

    async def get_corporation(self, corporation_id):
        return CorporationInfo(name="Test Corp", ceo_id=CHAR_ID, ticker="T")

    async def get_character_roles(self, character_id, access_token):
        return []

    # --- contract reads ---
    async def get_corporation_contracts(self, corporation_id, access_token):
        if self.forbid:
            raise CorporationContractsForbidden()
        return list(self._contracts)

    async def get_corporation_contract_items(
        self, corporation_id, contract_id, access_token
    ):
        self.item_fetches.append(contract_id)
        return list(self._items.get(contract_id, []))


def _contract(
    contract_id: int,
    *,
    title: str | None,
    status: str = "outstanding",
    price: Decimal = Decimal("1000.00"),
    location: int | None = int(LOCATION),
    completed: datetime | None = None,
    expired: datetime | None = None,
    issued: datetime = ISSUED,
) -> CorporationContract:
    return CorporationContract(
        contract_id=contract_id,
        type="item_exchange",
        status=status,
        title=title,
        price=price,
        start_location_id=location,
        issuer_id=CHAR_ID,
        acceptor_id=0,
        date_issued=issued,
        date_completed=completed,
        date_expired=expired,
    )


async def _connect(esi: ContractsEsi) -> uuid.UUID:
    async with SessionLocal() as session:
        corp = await corporations_repo.create_corporation(
            session, eve_corporation_id=CORP_EVE_ID, name="Test Corp",
            ceo_character_id=CHAR_ID, registered_by_character_id=CHAR_ID,
        )
        await session.commit()
        corp_uuid = corp.id
    async with SessionLocal() as session:
        await corp_esi_token_app.complete_corp_esi_authorize(
            session, FakeSso(), esi, code="c", verifier="v",
            user=_user("ceo"), cipher=CIPHER,
        )
    return corp_uuid


async def _make_appraisal(
    public_id: str,
    *,
    accepted_total: Decimal = Decimal("1000.00"),
    location: str | None = LOCATION,
    items: dict[int, int] | None = None,
) -> uuid.UUID:
    items = items if items is not None else {34: 100}
    lines = [
        {
            "type_id": tid,
            "type_name": f"Type {tid}",
            "quantity": qty,
            "status": "accepted",
            "basis": "buy",
            "percentage": Decimal("90"),
            "unit_value": Decimal("1"),
            "unit_price": Decimal("0.9"),
            "line_total": Decimal("90"),
            "reason": None,
        }
        for tid, qty in items.items()
    ]
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_EVE_ID)
        await appraisals_repo.create_appraisal(
            session, public_id=public_id, corporation_id=corp.id,
            created_by_character_id=CHAR_ID, market_hub_id=LOCATION,
            delivery_location_id=location, delivery_location_name="Home",
            accepted_total=accepted_total, rejected_count=0,
            request_json={"items": []}, lines=lines,
        )
        await session.commit()
    # Resolve the UUID for assertions.
    async with SessionLocal() as session:
        id_map = await links_repo.appraisal_public_id_to_id(
            session, corporation_id=corp.id
        )
    return id_map[public_id]


async def _run(esi: ContractsEsi, *, now: datetime = NOW) -> None:
    async with SessionLocal() as session:
        await contracts_app.refresh_contracts(
            session, FakeSso(), esi, corporation_id=CORP_EVE_ID,
            cipher=CIPHER, now=now,
        )


async def _status(public_id: str) -> str | None:
    async with SessionLocal() as session:
        rec = await appraisals_repo.get_by_public_id(session, public_id)
    return rec.contract_status


# --- matching + lifecycle ---


async def test_outstanding_matching_marks_in_progress():
    esi = ContractsEsi()
    await _connect(esi)
    await _make_appraisal("apprONEaaaaa")
    esi._contracts = [_contract(1, title="apprONEaaaaa")]
    esi._items = {1: [ContractItem(type_id=34, quantity=100)]}

    await _run(esi)

    assert await _status("apprONEaaaaa") == "in_progress"


async def test_finished_matching_marks_completed_with_timestamp():
    esi = ContractsEsi()
    await _connect(esi)
    a_id = await _make_appraisal("apprTWObbbbb")
    done = NOW - timedelta(minutes=5)
    esi._contracts = [
        _contract(2, title="please apprTWObbbbb thanks", status="finished",
                  completed=done)
    ]
    esi._items = {2: [ContractItem(type_id=34, quantity=100)]}

    await _run(esi)

    assert await _status("apprTWObbbbb") == "completed"
    async with SessionLocal() as session:
        link = await links_repo.get_for_appraisal(session, appraisal_id=a_id)
    assert link.completed_at == done


async def test_matching_id_but_wrong_items_is_mismatch():
    esi = ContractsEsi()
    await _connect(esi)
    await _make_appraisal("apprMISmatch", items={34: 100})
    # Cites the appraisal, right price + location, but a short quantity.
    esi._contracts = [_contract(3, title="apprMISmatch")]
    esi._items = {3: [ContractItem(type_id=34, quantity=99)]}

    await _run(esi)

    assert await _status("apprMISmatch") == "mismatch"


async def test_wrong_price_is_mismatch():
    esi = ContractsEsi()
    await _connect(esi)
    await _make_appraisal("apprPRICewr", accepted_total=Decimal("1000.00"))
    esi._contracts = [_contract(4, title="apprPRICewr", price=Decimal("1.00"))]
    esi._items = {4: [ContractItem(type_id=34, quantity=100)]}

    await _run(esi)

    assert await _status("apprPRICewr") == "mismatch"


async def test_voided_contract_surfaced_without_item_fetch():
    esi = ContractsEsi()
    await _connect(esi)
    await _make_appraisal("apprREJECTd")
    esi._contracts = [_contract(5, title="apprREJECTd", status="rejected")]

    await _run(esi)

    assert await _status("apprREJECTd") == "rejected"
    # A void contract is taken at face value — no items fetched to validate it.
    assert esi.item_fetches == []


async def test_outstanding_past_expiry_is_expired():
    esi = ContractsEsi()
    await _connect(esi)
    await _make_appraisal("apprEXPIRED")
    esi._contracts = [
        _contract(6, title="apprEXPIRED", status="outstanding",
                  expired=NOW - timedelta(days=1))
    ]

    await _run(esi)

    assert await _status("apprEXPIRED") == "expired"
    assert esi.item_fetches == []  # voided → not validated


async def test_deleted_contract_drops_the_link():
    esi = ContractsEsi()
    await _connect(esi)
    await _make_appraisal("apprGONEaaaa")
    esi._contracts = [_contract(7, title="apprGONEaaaa")]
    esi._items = {7: [ContractItem(type_id=34, quantity=100)]}
    await _run(esi)
    assert await _status("apprGONEaaaa") == "in_progress"

    # The contract is deleted in EVE → the link is reconciled away.
    esi._contracts = [_contract(7, title="apprGONEaaaa", status="deleted")]
    await _run(esi)
    assert await _status("apprGONEaaaa") is None


async def test_recontract_prefers_active_over_voided():
    esi = ContractsEsi()
    await _connect(esi)
    await _make_appraisal("apprRETRYaa")
    # An earlier rejected attempt and a fresh outstanding one cite the same appraisal.
    esi._contracts = [
        _contract(8, title="apprRETRYaa", status="rejected",
                  issued=ISSUED - timedelta(hours=1)),
        _contract(9, title="apprRETRYaa", status="outstanding", issued=ISSUED),
    ]
    esi._items = {9: [ContractItem(type_id=34, quantity=100)]}

    await _run(esi)

    assert await _status("apprRETRYaa") == "in_progress"


async def test_unmatched_contract_links_nothing():
    esi = ContractsEsi()
    await _connect(esi)
    await _make_appraisal("apprLONELYz")
    # A contract whose title cites no known appraisal.
    esi._contracts = [_contract(10, title="some other deal")]

    await _run(esi)

    assert await _status("apprLONELYz") is None


async def test_403_does_not_flag_token(caplog):
    import logging

    esi = ContractsEsi(forbid=True)
    corp_uuid = await _connect(esi)
    await _make_appraisal("appr403aaaa")

    with caplog.at_level(logging.WARNING, logger="app.application.corp_contracts"):
        await _run(esi)  # must not raise

    assert await _status("appr403aaaa") is None
    async with SessionLocal() as session:
        token = await tokens_repo.get_for_corp(session, corp_uuid)
    # A scope/role 403 is not a refresh failure — the token stays healthy (#68).
    assert token.last_refresh_failed_at is None


# --- list / detail ordering ---


async def test_history_orders_by_status_then_recency():
    esi = ContractsEsi()
    corp_uuid = await _connect(esi)
    # One appraisal per status bucket (+ one with no contract).
    await _make_appraisal("sDONEcccccc")  # completed
    await _make_appraisal("sPROGdddddd")  # in_progress
    await _make_appraisal("sMISMeeeeee")  # mismatch
    await _make_appraisal("sVOIDffffff")  # rejected (voided)
    await _make_appraisal("sNONEgggggg")  # no contract

    esi._contracts = [
        _contract(20, title="sDONEcccccc", status="finished",
                  completed=NOW - timedelta(minutes=1)),
        _contract(21, title="sPROGdddddd", status="outstanding"),
        _contract(22, title="sMISMeeeeee", status="outstanding"),
        _contract(23, title="sVOIDffffff", status="rejected"),
    ]
    esi._items = {
        20: [ContractItem(type_id=34, quantity=100)],
        21: [ContractItem(type_id=34, quantity=100)],
        22: [ContractItem(type_id=34, quantity=1)],  # wrong qty → mismatch
    }
    await _run(esi)

    async with SessionLocal() as session:
        rows = await appraisals_repo.list_for_corp(session, corp_uuid)
    order = [(r.public_id, r.contract_status) for r in rows]
    assert order == [
        ("sPROGdddddd", "in_progress"),
        ("sMISMeeeeee", "mismatch"),
        ("sDONEcccccc", "completed"),
        ("sVOIDffffff", "rejected"),
        ("sNONEgggggg", None),
    ]
