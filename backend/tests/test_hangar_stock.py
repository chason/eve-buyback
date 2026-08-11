"""ADR-0050: the persisted hangar snapshot and the asset-first Stock view — the
reconcile pass photographs the marked hangars (empty is a valid photograph), the
inventory view keys its rows off that photograph with the ledger joined FIFO for
cost and age, sell-order stock rides in its own `listed` section, the pre-ADR
ledger view remains the fallback, and unmarking the last hangar deletes the
stored snapshot (the privacy promise)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.application import corp_esi_token as corp_esi_token_app
from app.application import hangar as hangar_app
from app.application import lots as lots_app
from app.application import reconciliation as recon_app
from app.application.auth import AuthenticatedUser
from app.data.db import SessionLocal
from app.data.models import CorpMarketOrder, MarketPrice
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import buyback_locations as locations_repo
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.data.repositories import hangar_stock as hangar_stock_repo
from app.data.repositories import hangars as hangars_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import sde as sde_repo
from app.main import app
from app.plugins.esi import CharacterInfo, CorporationAssetsForbidden, CorporationInfo
from app.plugins.sso import OAuthToken, VerifiedCharacter
from app.plugins.token_cipher import get_token_cipher
from tests.helpers import CHAR_ID, CORP_ID

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
JITA = "60003760"
AMARR = "60008494"
TRIT = 34
PYE = 35
STALE_DAYS = 60
TAX = Decimal("0.036")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


class FakeSso:
    configured = True

    def build_authorize_url(self, *, state, code_challenge, scopes=None):
        return f"https://login.eveonline.com/authorize?state={state}"

    async def exchange_code(self, code, code_verifier):
        return OAuthToken(access_token="a", refresh_token="r")

    async def verify_token(self, access_token):
        return VerifiedCharacter(character_id=CHAR_ID, name="Boss")

    async def refresh_access_token(self, refresh_token):
        return OAuthToken(access_token="fresh", refresh_token=refresh_token)


class AssetsEsi:
    """ESI fake (the test_reconciliation harness): `hangar` is the stock in the
    marked hangar (CorpSAG2 at Jita) as {type_id: qty}."""

    def __init__(self):
        self.hangar: dict[int, int] = {}
        self.forbid = False
        self._item_id = iter(range(7_000_000, 8_000_000))

    async def get_character(self, character_id):
        return CharacterInfo(name="Boss", corporation_id=CORP_ID)

    async def get_character_corporation(self, character_id):
        return CORP_ID

    async def get_corporation(self, corporation_id):
        return CorporationInfo(name="Test Corp", ceo_id=CHAR_ID, ticker="T")

    async def get_character_roles(self, character_id, access_token):
        return []

    async def get_corporation_assets(self, corporation_id, access_token):
        if self.forbid:
            raise CorporationAssetsForbidden()
        from app.plugins.esi import CorporationAsset

        # The real ESI shape: hangar rows hang under the corp's OFFICE at the
        # station — only the office row points at the station itself.
        office_id = 1_027_000_000_001
        stacks = [CorporationAsset(
            item_id=office_id, type_id=27, quantity=1,
            location_id=int(JITA), location_flag="OfficeFolder",
        )]
        for tid, qty in self.hangar.items():
            stacks.append(CorporationAsset(
                item_id=next(self._item_id), type_id=tid, quantity=qty,
                location_id=office_id, location_flag="CorpSAG2",
            ))
        return stacks


def _user() -> AuthenticatedUser:
    return AuthenticatedUser(
        character_id=CHAR_ID, character_name="Boss", corporation_id=CORP_ID,
        corporation_name="Test Corp", role="ceo", is_director=False,
        corporation_registered=True,
    )


async def _seed(esi: AssetsEsi, *, prices: dict[int, str] | None = None) -> None:
    async with SessionLocal() as session:
        corp = await corporations_repo.create_corporation(
            session, eve_corporation_id=CORP_ID, name="Test Corp",
            ceo_character_id=CHAR_ID, registered_by_character_id=CHAR_ID,
        )
        await entitlements_repo.upsert(
            session, corporation_id=corp.id, feature="accounting",
            source="admin", expires_at=None,
        )
        await config_repo.upsert_config(
            session, corporation_id=corp.id, market_hub_id=JITA,
            default_basis="buy", default_percentage=90,
            aggregate_field="percentile",
        )
        await locations_repo.add(
            session, corp.id, kind="npc_station", location_id=JITA,
            name="Jita IV - Moon 4", system_name="Jita",
        )
        await hangars_repo.add(
            session, corporation_id=corp.id, location_id=JITA,
            location_name="Jita IV - Moon 4", division=2,
        )
        await sde_repo.bulk_upsert_types(session, [
            {"type_id": TRIT, "name": "Tritanium", "group_id": 18,
             "market_group_id": 1857, "volume": 0.01, "published": True},
            {"type_id": PYE, "name": "Pyerite", "group_id": 18,
             "market_group_id": 1857, "volume": 0.01, "published": True},
        ])
        for type_id, buy in (prices or {}).items():
            b = Decimal(buy)
            session.add(MarketPrice(
                hub_id=JITA, type_id=type_id,
                buy_weighted_average=b, buy_max=b, buy_min=b, buy_median=b,
                buy_percentile=b, buy_volume=Decimal(1000), buy_order_count=10,
                sell_weighted_average=b, sell_max=b, sell_min=b, sell_median=b,
                sell_percentile=b, sell_volume=Decimal(1000), sell_order_count=10,
                fetched_at=NOW,
            ))
        await session.commit()
    async with SessionLocal() as session:
        await corp_esi_token_app.complete_corp_esi_authorize(
            session, FakeSso(), esi, code="c", verifier="v",
            user=_user(), cipher=get_token_cipher(),
        )


async def _run(esi: AssetsEsi):
    async with SessionLocal() as session:
        return await recon_app.reconcile_hangars(
            session, FakeSso(), esi, corporation_eve_id=CORP_ID,
            cipher=get_token_cipher(), excess_flag_isk=1_000_000_000, now=NOW,
        )


async def _corp_id():
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        return corp.id


async def _add_lot(type_id: int, qty: int, cost: str, *, acquired=NOW,
                   location=JITA) -> None:
    async with SessionLocal() as session:
        corp = await corporations_repo.get_by_eve_id(session, CORP_ID)
        await lots_repo.create_lot(
            session, corporation_id=corp.id, item_type_id=type_id, qty=qty,
            unit_purchase_cost=Decimal(cost), acquired_at=acquired,
            source="buyback", location_id=location,
        )
        await session.commit()


async def _inventory() -> lots_app.InventoryView:
    async with SessionLocal() as session:
        return await lots_app.get_inventory(
            session, corporation_eve_id=CORP_ID, stale_days=STALE_DAYS,
            sales_tax_rate=TAX, now=NOW,
        )


# --- the snapshot the reconcile pass persists --------------------------------------


async def test_reconcile_persists_the_snapshot_it_counted():
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    await _add_lot(TRIT, 1000, "5.25")
    esi.hangar = {TRIT: 1000}  # matches the books: no deltas at all

    await _run(esi)

    async with SessionLocal() as session:
        corp_id = await _corp_id()
        rows = await hangar_stock_repo.list_for_corp(session, corporation_id=corp_id)
        stamped = await hangar_stock_repo.synced_at(session, corporation_id=corp_id)
    assert [(r.location_id, r.type_id, r.qty) for r in rows] == [(JITA, TRIT, 1000)]
    assert stamped == NOW


async def test_an_empty_hangar_is_a_valid_snapshot():
    esi = AssetsEsi()
    await _seed(esi)
    esi.hangar = {}

    await _run(esi)

    async with SessionLocal() as session:
        corp_id = await _corp_id()
        rows = await hangar_stock_repo.list_for_corp(session, corporation_id=corp_id)
        stamped = await hangar_stock_repo.synced_at(session, corporation_id=corp_id)
    assert rows == [] and stamped == NOW


async def test_a_failed_read_leaves_the_previous_snapshot_in_place():
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    esi.hangar = {TRIT: 700}
    await _run(esi)

    esi.forbid = True
    with pytest.raises(CorporationAssetsForbidden):
        await _run(esi)

    async with SessionLocal() as session:
        corp_id = await _corp_id()
        rows = await hangar_stock_repo.list_for_corp(session, corporation_id=corp_id)
    assert [(r.type_id, r.qty) for r in rows] == [(TRIT, 700)]


async def test_unmarking_the_last_hangar_deletes_the_snapshot():
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    esi.hangar = {TRIT: 500}
    await _run(esi)

    async with SessionLocal() as session:
        await hangar_app.remove_hangar(
            session, corporation_eve_id=CORP_ID, location_id=JITA, division=2
        )
    async with SessionLocal() as session:
        corp_id = await _corp_id()
        rows = await hangar_stock_repo.list_for_corp(session, corporation_id=corp_id)
        stamped = await hangar_stock_repo.synced_at(session, corporation_id=corp_id)
    assert rows == [] and stamped is None


# --- the asset-first inventory view ------------------------------------------------


async def test_inventory_shows_the_ledger_until_a_snapshot_exists():
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    await _add_lot(TRIT, 1000, "5.25")

    inv = await _inventory()

    assert inv.basis == "ledger" and inv.as_of is None
    assert [(i.type_id, i.qty) for i in inv.items] == [(TRIT, 1000)]


async def test_hangar_rows_show_whats_physically_there_with_ledger_cost_and_age():
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    old = NOW - timedelta(days=90)
    await _add_lot(TRIT, 1000, "5.25", acquired=old)
    esi.hangar = {TRIT: 600}  # 400 units left off-app: the row shows the 600
    await _run(esi)

    inv = await _inventory()

    assert inv.basis == "hangar" and inv.as_of == NOW
    (item,) = inv.items
    assert (item.qty, item.qty_unbooked) == (600, 0)
    # Cost/age come from the matched FIFO portion — 600 of the 90-day-old lot.
    assert item.total_cost == Decimal("3150.00")  # 600 × 5.25
    assert item.oldest_days == 90 and item.stale is True
    assert item.lots[0].qty == 600
    # The cards stay ledger-wide: all 1000 units are still owned on paper.
    assert inv.total_cost == Decimal("5250.00")


async def test_stock_with_no_ledger_entry_shows_unbooked_with_unknown_age():
    esi = AssetsEsi()
    await _seed(esi, prices={PYE: "8.00"})
    async with SessionLocal() as session:  # snapshot without booking: bypass the
        corp_id = await _corp_id()  # reconcile (it would book a deemed lot)
        await hangar_stock_repo.replace_for_corp(
            session, corporation_id=corp_id,
            counts={(JITA, PYE): 250}, synced_at=NOW,
        )
        await session.commit()

    inv = await _inventory()

    (item,) = inv.items
    assert (item.qty, item.qty_unbooked) == (250, 250)
    assert item.total_cost == Decimal(0) and item.oldest_days is None
    assert item.lots == []
    # Worth prices what's there; unrealized has no booked basis to move against.
    assert item.worth == 250 * Decimal("8.00") * (1 - TAX)
    assert item.unrealized == Decimal(0)


async def test_a_partially_booked_row_shows_matched_cost_and_the_unbooked_rest():
    """More in the hangar than the books explain: the row carries the matched
    portion's cost and age plus the unbooked remainder (ADR-0050)."""
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    await _add_lot(TRIT, 600, "5.25", acquired=NOW - timedelta(days=10))
    async with SessionLocal() as session:
        corp_id = await _corp_id()
        await hangar_stock_repo.replace_for_corp(
            session, corporation_id=corp_id,
            counts={(JITA, TRIT): 1000}, synced_at=NOW,
        )
        await session.commit()

    inv = await _inventory()

    (item,) = inv.items
    assert (item.qty, item.qty_unbooked) == (1000, 400)
    assert item.total_cost == Decimal("3150.00")  # the booked 600 × 5.25
    assert item.oldest_days == 10  # age comes from what IS booked
    assert (item.lots[0].qty, len(item.lots)) == (600, 1)
    # Unrealized moves only the booked portion against its cost.
    unit = Decimal("4.00") * (1 - TAX)
    assert item.worth == 1000 * unit
    assert item.unrealized == 600 * unit - Decimal("3150.00")


async def test_one_type_across_two_hangars_merges_into_one_fifo_row():
    """The same mineral in two marked hangars is one table row: quantities sum,
    and the backing lots interleave oldest-first across locations (ADR-0050)."""
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    old = NOW - timedelta(days=30)
    new = NOW - timedelta(days=3)
    await _add_lot(TRIT, 100, "5.00", acquired=old, location=JITA)
    await _add_lot(TRIT, 200, "6.00", acquired=new, location=AMARR)
    async with SessionLocal() as session:
        corp_id = await _corp_id()
        await hangar_stock_repo.replace_for_corp(
            session, corporation_id=corp_id,
            counts={(JITA, TRIT): 100, (AMARR, TRIT): 200}, synced_at=NOW,
        )
        await session.commit()

    inv = await _inventory()

    (item,) = inv.items
    assert (item.qty, item.qty_unbooked) == (300, 0)
    assert item.total_cost == Decimal("1700.00")  # 100×5 + 200×6
    assert item.oldest_days == 30
    assert [lot.qty for lot in item.lots] == [100, 200]  # oldest first
    assert item.lots[0].acquired_at == old


async def test_valuation_cards_follow_the_ledger_not_the_table():
    """An empty hangar must not hide a priced ledger (review fix): the cards'
    gate and the totals share the open-lot population."""
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    await _add_lot(TRIT, 1000, "5.25")
    esi.hangar = {}  # everything hauled off / listed — hangar photographs empty

    # The reconcile flags the shortfall; the empty snapshot still persists.
    await _run(esi)
    inv = await _inventory()

    assert inv.basis == "hangar" and inv.items == []
    assert inv.anything_priced is True
    assert inv.worth_total == 1000 * Decimal("4.00") * (1 - TAX)
    assert inv.unpriced_types == 0  # no table rows → nothing to footnote


async def test_sell_order_stock_rides_in_its_own_listed_section():
    esi = AssetsEsi()
    await _seed(esi, prices={TRIT: "4.00"})
    await _add_lot(TRIT, 1000, "5.25")
    esi.hangar = {TRIT: 1000}
    await _run(esi)
    async with SessionLocal() as session:
        corp_id = await _corp_id()
        session.add(CorpMarketOrder(
            corporation_id=corp_id, order_id=1, type_id=TRIT,
            is_buy_order=False, price=Decimal("6.00"), volume_remain=200,
            volume_total=200, location_id=AMARR, wallet_division=1, issued=NOW,
        ))
        await session.commit()

    inv = await _inventory()

    (row,) = inv.listed
    assert (row.type_id, row.location_id, row.qty) == (TRIT, AMARR, 200)
    assert row.worth == 200 * Decimal("4.00") * (1 - TAX)
