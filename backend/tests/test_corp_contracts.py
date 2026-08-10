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
from app.data.models import MarketPrice
from app.data.repositories import appraisal_contracts as links_repo
from app.data.repositories import appraisals as appraisals_repo
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import corp_esi_token as tokens_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import sales as sales_repo
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
    issuer_corporation_id: int | None = None,
    for_corporation: bool = False,
) -> CorporationContract:
    return CorporationContract(
        contract_id=contract_id,
        type="item_exchange",
        status=status,
        title=title,
        price=price,
        start_location_id=location,
        issuer_id=CHAR_ID,
        issuer_corporation_id=issuer_corporation_id,
        for_corporation=for_corporation,
        acceptor_id=0,
        date_issued=issued,
        date_completed=completed,
        date_expired=expired,
    )


def _outgoing(
    contract_id: int,
    *,
    price: Decimal,
    status: str = "finished",
    completed: datetime | None = None,
) -> CorporationContract:
    """A contract the corp itself issued on its own behalf — an outgoing sale
    candidate (ADR-0045, #157)."""
    return _contract(
        contract_id,
        title=None,
        status=status,
        price=price,
        completed=completed or NOW - timedelta(minutes=5),
        issuer_corporation_id=CORP_EVE_ID,
        for_corporation=True,
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
    rejected_items: dict[int, int] | None = None,
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
    ] + [
        {
            "type_id": tid,
            "type_name": f"Type {tid}",
            "quantity": qty,
            "status": "rejected",
            "basis": None,
            "percentage": None,
            "unit_value": None,
            "unit_price": None,
            "line_total": Decimal(0),
            "reason": "not accepted",
        }
        for tid, qty in (rejected_items or {}).items()
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


# --- lot materialization (ADR-0043, #151) ---


async def _lots(corp_uuid: uuid.UUID):
    async with SessionLocal() as session:
        return await lots_repo.open_lots(session, corporation_id=corp_uuid)


async def test_completed_contract_materializes_one_lot_per_accepted_line():
    esi = ContractsEsi()
    corp_uuid = await _connect(esi)
    # Two accepted lines and a rejected one (rejected items never enter inventory).
    a_id = await _make_appraisal(
        "apprLOTSaaaa", items={34: 100, 35: 50}, rejected_items={608: 1}
    )
    done = NOW - timedelta(minutes=5)
    esi._contracts = [
        _contract(30, title="apprLOTSaaaa", status="finished", completed=done)
    ]
    esi._items = {
        30: [ContractItem(type_id=34, quantity=100),
             ContractItem(type_id=35, quantity=50)]
    }

    await _run(esi)

    lots = await _lots(corp_uuid)
    assert {(lot.item_type_id, lot.qty_remaining) for lot in lots} == {
        (34, 100),
        (35, 50),
    }
    for lot in lots:
        assert lot.source == "buyback"
        assert lot.appraisal_id == a_id
        assert lot.unit_purchase_cost == Decimal("0.9")
        assert lot.unit_hauling_cost == Decimal(0)
        assert lot.cost_is_estimated is False  # verified cost, ADR-0043
        assert lot.location_id == LOCATION
        assert lot.acquired_at == done


async def test_rerunning_the_watcher_creates_no_duplicate_lots():
    esi = ContractsEsi()
    corp_uuid = await _connect(esi)
    await _make_appraisal("apprONCEaaaa")
    esi._contracts = [
        _contract(31, title="apprONCEaaaa", status="finished",
                  completed=NOW - timedelta(minutes=5))
    ]
    esi._items = {31: [ContractItem(type_id=34, quantity=100)]}

    await _run(esi)
    await _run(esi)  # the watcher fires repeatedly; lots are created once

    lots = await _lots(corp_uuid)
    assert [(lot.item_type_id, lot.qty_remaining) for lot in lots] == [(34, 100)]


async def test_unfinished_and_mismatched_contracts_create_no_lots():
    esi = ContractsEsi()
    corp_uuid = await _connect(esi)
    await _make_appraisal("apprLIVEaaaa")
    await _make_appraisal("apprBADQaaaa", items={34: 100})
    esi._contracts = [
        # Still outstanding — nothing bought yet.
        _contract(32, title="apprLIVEaaaa", status="outstanding"),
        # Finished but short on items → mismatch, not a verified purchase.
        _contract(33, title="apprBADQaaaa", status="finished",
                  completed=NOW - timedelta(minutes=5)),
    ]
    esi._items = {
        32: [ContractItem(type_id=34, quantity=100)],
        33: [ContractItem(type_id=34, quantity=99)],
    }

    await _run(esi)

    assert await _status("apprLIVEaaaa") == "in_progress"
    assert await _status("apprBADQaaaa") == "mismatch"
    assert await _lots(corp_uuid) == []


async def test_missing_completed_timestamp_falls_back_to_now():
    esi = ContractsEsi()
    corp_uuid = await _connect(esi)
    await _make_appraisal("apprNOTSaaaa")
    esi._contracts = [
        _contract(34, title="apprNOTSaaaa", status="finished", completed=None)
    ]
    esi._items = {34: [ContractItem(type_id=34, quantity=100)]}

    await _run(esi)

    lots = await _lots(corp_uuid)
    assert [lot.acquired_at for lot in lots] == [NOW]


# --- outgoing contract sales (ADR-0045, #157) ---


async def _entitle_and_stock(
    corp_uuid, *, qty=100, cost="3.60", type_id=34, prices=None
):
    """Give the corp the accounting entitlement, a lot at LOCATION, and (optionally)
    cached prices at a Jita default hub for proceeds allocation."""
    async with SessionLocal() as session:
        await entitlements_repo.upsert(
            session, corporation_id=corp_uuid, feature="accounting",
            source="admin", expires_at=None,
        )
        await config_repo.upsert_config(
            session, corporation_id=corp_uuid, market_hub_id="60003760",
            default_basis="buy", default_percentage=90,
            aggregate_field="percentile",
        )
        lot = await lots_repo.create_lot(
            session, corporation_id=corp_uuid, item_type_id=type_id, qty=qty,
            unit_purchase_cost=Decimal(cost), acquired_at=ISSUED,
            source="buyback", location_id=LOCATION,
        )
        for tid, buy in (prices or {}).items():
            b = Decimal(buy)
            session.add(MarketPrice(
                hub_id="60003760", type_id=tid,
                buy_weighted_average=b, buy_max=b, buy_min=b, buy_median=b,
                buy_percentile=b, buy_volume=Decimal(1000), buy_order_count=10,
                sell_weighted_average=b, sell_max=b, sell_min=b, sell_median=b,
                sell_percentile=b, sell_volume=Decimal(1000), sell_order_count=10,
                fetched_at=NOW,
            ))
        await session.commit()
        return lot


async def _sales(corp_uuid):
    async with SessionLocal() as session:
        return await sales_repo.list_for_corp(session, corporation_id=corp_uuid)


async def test_outgoing_finished_contract_records_a_fifo_sale():
    esi = ContractsEsi()
    corp_uuid = await _connect(esi)
    lot = await _entitle_and_stock(corp_uuid, qty=100, cost="3.60")
    esi._contracts = [_outgoing(40, price=Decimal("500.00"))]
    esi._items = {40: [ContractItem(type_id=34, quantity=100)]}

    await _run(esi)

    rows = await _sales(corp_uuid)
    assert [(r.lot_id, r.qty, r.unit_proceeds, r.channel, r.external_ref)
            for r in rows] == [
        (lot.id, 100, Decimal("5.00"), "contract", 40),
    ]
    async with SessionLocal() as session:
        remaining = await lots_repo.open_lots(session, corporation_id=corp_uuid)
    assert remaining == []  # the lot was consumed by the sale

    # The watcher fires repeatedly; the contract records once.
    await _run(esi)
    assert len(await _sales(corp_uuid)) == 1


async def test_outgoing_contract_allocates_price_across_items_by_value():
    esi = ContractsEsi()
    corp_uuid = await _connect(esi)
    await _entitle_and_stock(
        corp_uuid, qty=100, cost="3.60", type_id=34,
        prices={34: "4.00", 35: "8.00"},
    )
    async with SessionLocal() as session:
        await lots_repo.create_lot(
            session, corporation_id=corp_uuid, item_type_id=35, qty=50,
            unit_purchase_cost=Decimal("7.20"), acquired_at=ISSUED,
            source="buyback", location_id=LOCATION,
        )
        await session.commit()
    # Trit value 100×4=400 vs Pye 50×8=400 → the 500 ISK price splits 250/250.
    esi._contracts = [_outgoing(41, price=Decimal("500.00"))]
    esi._items = {41: [
        ContractItem(type_id=34, quantity=100),
        ContractItem(type_id=35, quantity=50),
    ]}

    await _run(esi)

    rows = await _sales(corp_uuid)
    proceeds = {
        r.lot_id: (r.qty, r.unit_proceeds) for r in rows
    }
    totals = {qty * unit for qty, unit in proceeds.values()}
    assert totals == {Decimal("250.00")}
    assert sum(qty * unit for qty, unit in proceeds.values()) == Decimal("500.00")


async def test_outgoing_contract_needs_the_entitlement():
    esi = ContractsEsi()
    corp_uuid = await _connect(esi)
    # Stock but NO entitlement: the watcher still runs, records no sale.
    async with SessionLocal() as session:
        await lots_repo.create_lot(
            session, corporation_id=corp_uuid, item_type_id=34, qty=100,
            unit_purchase_cost=Decimal("3.60"), acquired_at=ISSUED,
            source="buyback", location_id=LOCATION,
        )
        await session.commit()
    esi._contracts = [_outgoing(42, price=Decimal("500.00"))]
    esi._items = {42: [ContractItem(type_id=34, quantity=100)]}

    await _run(esi)

    assert await _sales(corp_uuid) == []


async def test_zero_price_outgoing_contract_is_not_a_sale():
    esi = ContractsEsi()
    corp_uuid = await _connect(esi)
    await _entitle_and_stock(corp_uuid)
    # A giveaway / internal move: items left, no ISK — the hangar check's problem,
    # not a revenue event.
    esi._contracts = [_outgoing(43, price=Decimal(0))]
    esi._items = {43: [ContractItem(type_id=34, quantity=100)]}

    await _run(esi)

    assert await _sales(corp_uuid) == []


async def test_incoming_buyback_contract_still_creates_lots_never_sales():
    esi = ContractsEsi()
    corp_uuid = await _connect(esi)
    await _entitle_and_stock(corp_uuid, qty=1)  # entitled; tiny unrelated lot
    await _make_appraisal("apprINBOUNDa")
    # The member issued it TO the corp — issuer_corporation_id is NOT the corp.
    esi._contracts = [
        _contract(44, title="apprINBOUNDa", status="finished",
                  completed=NOW - timedelta(minutes=5),
                  issuer_corporation_id=98000999),
    ]
    esi._items = {44: [ContractItem(type_id=34, quantity=100)]}

    await _run(esi)

    assert await _status("apprINBOUNDa") == "completed"
    rows = await _sales(corp_uuid)
    assert rows == []  # buying is never selling
    async with SessionLocal() as session:
        lots = await lots_repo.open_lots(session, corporation_id=corp_uuid)
    assert any(lot.appraisal_id is not None for lot in lots)  # the purchase landed


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
