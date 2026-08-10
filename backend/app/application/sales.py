"""Market-sales ingestion (ADR-0045, #156): read the configured buyback wallet
division's fills and journal plus the corp's live market orders, and turn them into
ledger facts — FIFO-consuming sales, tax attached to fills, broker fees as expenses,
the "listed" order snapshot, and the guard events for anything unexpected.

A corp with no configured wallet division no-ops entirely: the division is the
opt-in switch for the whole sell side (ADR-0045).
"""

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.application import corp_esi_token as corp_esi_token_app
from app.application import entitlements as entitlements_app
from app.application import reconciliation as reconciliation_app
from app.application import transformations as transformations_app
from app.application.corporations import get_registered_corporation
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import expenses as expenses_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import market_orders as orders_repo
from app.data.repositories import reconciliation as recon_repo
from app.data.repositories import sales as sales_repo
from app.data.repositories.market_orders import OrderRow
from app.domain.lots import LotConsumption, OpenLot, SaleChannel, plan_fifo
from app.domain.transformations import allocate_amount
from app.plugins.esi import (
    ContractItem,
    CorpMarketOrder,
    CorporationContract,
    EsiClient,
)
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher

log = logging.getLogger(__name__)

# Journal ref types the ingestion books (ADR-0045).
_TAX_REF = "transaction_tax"
_BROKER_REFS = frozenset({"brokers_fee", "market_provider_tax"})


def _order_row(order: CorpMarketOrder) -> OrderRow:
    """Bridge the ESI order model into the snapshot repo's input contract (#188)."""
    return {
        "order_id": order.order_id,
        "type_id": order.type_id,
        "is_buy_order": order.is_buy_order,
        "price": order.price,
        "volume_remain": order.volume_remain,
        "volume_total": order.volume_total,
        "location_id": str(order.location_id),
        "wallet_division": order.wallet_division,
        "issued": order.issued,
    }


class SalesIngestResult(BaseModel):
    sales_recorded: int
    fees_booked: int
    flagged: int


async def ingest_market_sales(
    session: AsyncSession,
    sso: EveSsoClient,
    esi: EsiClient,
    *,
    corporation_eve_id: int,
    cipher: TokenCipher,
    now: datetime | None = None,
) -> SalesIngestResult:
    """One ingestion pass for one corp (ADR-0045). Idempotent: fills dedupe on
    `transaction_id`, fees on the journal id, tax attachment SETs rather than adds,
    and the order snapshot is a replace. Owns the commit.

    Raises token/scope exceptions as-is (`CorpEsiTokenMissing`/`Expired`,
    `CorporationWalletForbidden`, `CorporationOrdersForbidden`) — the job logs and
    skips without flagging the token failed (the ADR-0037 pattern)."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    config = await config_repo.get_config(session, corp.id)
    if config is None or config.wallet_division is None:
        return SalesIngestResult(sales_recorded=0, fees_booked=0, flagged=0)
    division = config.wallet_division
    now = now or datetime.now(UTC)

    access_token = await corp_esi_token_app.get_corp_esi_access_token(
        session, sso, corporation_uuid=corp.id, cipher=cipher
    )

    # --- live orders → the "listed" snapshot + the wrong-division guard ---------
    orders = await esi.get_corporation_orders(corporation_eve_id, access_token)
    await orders_repo.replace_for_corp(
        session,
        corporation_id=corp.id,
        orders=[_order_row(o) for o in orders],
    )
    flagged = await _flag_off_division_orders(
        session, corporation_id=corp.id, division=division, now=now
    )

    # --- fills → FIFO-consuming sales -------------------------------------------
    transactions = await esi.get_corporation_wallet_transactions(
        corporation_eve_id, division, access_token
    )
    seen = await sales_repo.external_refs_for_channel(
        session, corporation_id=corp.id, channel="market"
    )
    sales_recorded = 0
    # Oldest first, so FIFO consumption follows the actual order of events.
    for tx in sorted(transactions, key=lambda t: (t.date, t.transaction_id)):
        if tx.is_buy or tx.transaction_id in seen:
            continue
        unmatched = await consume_and_record_sale(
            session,
            corp.id,
            type_id=tx.type_id,
            qty=tx.quantity,
            unit_proceeds=tx.unit_price,
            location_id=str(tx.location_id),
            channel="market",
            external_ref=tx.transaction_id,
            sold_at=tx.date,
            now=now,
        )
        sales_recorded += 1
        if unmatched:
            flagged += 1

    # --- journal → tax on fills + broker fees as expenses ------------------------
    journal = await esi.get_corporation_wallet_journal(
        corporation_eve_id, division, access_token
    )
    fee_refs = await expenses_repo.external_refs(session, corporation_id=corp.id)
    fees_booked = 0
    for entry in journal:
        if entry.amount is None:
            continue
        if entry.ref_type == _TAX_REF and entry.context_id is not None:
            await sales_repo.set_sales_tax_for_ref(
                session,
                corporation_id=corp.id,
                channel="market",
                external_ref=entry.context_id,
                sales_tax=abs(entry.amount),
            )
        elif entry.ref_type in _BROKER_REFS and entry.id not in fee_refs:
            await expenses_repo.create_expense(
                session,
                corporation_id=corp.id,
                kind="broker_fee",
                amount=abs(entry.amount),
                source="esi",
                incurred_at=entry.date,
                external_ref=entry.id,
            )
            fees_booked += 1

    await session.commit()
    if sales_recorded or fees_booked or flagged:
        log.info(
            "sales ingest for corp %s: %d sale(s), %d fee(s), %d flagged",
            corporation_eve_id,
            sales_recorded,
            fees_booked,
            flagged,
        )
    return SalesIngestResult(
        sales_recorded=sales_recorded, fees_booked=fees_booked, flagged=flagged
    )


async def consume_and_record_sale(
    session: AsyncSession,
    corporation_id: uuid.UUID,
    *,
    type_id: int,
    qty: int,
    unit_proceeds: Decimal,
    location_id: str,
    channel: SaleChannel,
    external_ref: int,
    sold_at: datetime,
    now: datetime,
) -> bool:
    """Turn one detected disposal (a market fill or an outgoing contract's line)
    into FIFO-consuming sale rows — one per lot touched (ADR-0043). Stock the books
    didn't have — the no-lot sale (ADR-0045) — books a deemed-cost lot for the
    shortfall (`cost_is_estimated=TRUE`, so the estimate propagates into realized
    profit), consumes it, and flags the event in the reconciliation log. Returns
    True when that fallback fired. Does not commit; callers own the UoW."""
    lots = await lots_repo.open_lots(
        session,
        corporation_id=corporation_id,
        item_type_id=type_id,
        location_id=location_id,
    )
    open_lots: list[OpenLot] = []
    for lot in lots:
        open_lots.append(
            OpenLot(
                lot_id=lot.id,
                qty_remaining=lot.qty_remaining,
                acquired_at=lot.acquired_at,
            )
        )
    plan = plan_fifo(open_lots, qty)
    consumptions = list(plan.consumptions)
    unmatched = plan.shortfall > 0
    if unmatched:
        # The same deemed-cost policy the hangar reconciliation uses (ADR-0044/0045).
        deemed = await reconciliation_app.deemed_unit_costs(
            session, corporation_id, [type_id]
        )
        # No market evidence either → the sale's own proceeds are the only cost
        # signal left; deem at proceeds (zero estimated profit, never invented gain).
        unit_cost = deemed.get(type_id, unit_proceeds)
        shortfall_lot = await lots_repo.create_lot(
            session,
            corporation_id=corporation_id,
            item_type_id=type_id,
            qty=plan.shortfall,
            unit_purchase_cost=unit_cost,
            acquired_at=sold_at,
            source="opening_balance",
            cost_is_estimated=True,
            location_id=location_id,
            notes="Sold before the books knew we had it (ADR-0045)",
        )
        consumptions.append(
            LotConsumption(lot_id=shortfall_lot.id, qty=plan.shortfall)
        )
        await recon_repo.add_event(
            session,
            corporation_id=corporation_id,
            location_id=location_id,
            type_id=type_id,
            kind="unmatched_sale",
            qty=plan.shortfall,
            occurred_at=now,
            unit_cost=unit_cost,
            lot_id=shortfall_lot.id,
            flagged=True,
        )
    for consumption in consumptions:
        await lots_repo.consume(
            session, lot_id=consumption.lot_id, qty=consumption.qty
        )
        await sales_repo.create_sale(
            session,
            corporation_id=corporation_id,
            lot_id=consumption.lot_id,
            qty=consumption.qty,
            unit_proceeds=unit_proceeds,
            channel=channel,
            source="esi",
            sold_at=sold_at,
            external_ref=external_ref,
        )
    return unmatched


async def _flag_off_division_orders(
    session: AsyncSession,
    *,
    corporation_id: uuid.UUID,
    division: int,
    now: datetime,
) -> int:
    """The ADR-0045 guard: sell orders paying into a division other than the
    configured buyback one get a flagged log entry (traders pick the division per
    order — a fat-finger silently diverts proceeds). Dedupe per (location, type)
    against the latest event, like the other reconciliation kinds."""
    off = await orders_repo.sell_orders_off_division(
        session, corporation_id=corporation_id, division=division
    )
    if not off:
        return 0
    latest = await recon_repo.latest_by_slot(session, corporation_id=corporation_id)
    flagged = 0
    for location_id, type_id, qty, _price in off:
        last = latest.get((location_id, type_id))
        if (
            last is not None
            and last.kind == "unexpected_division"
            and last.qty == qty
        ):
            continue
        await recon_repo.add_event(
            session,
            corporation_id=corporation_id,
            location_id=location_id,
            type_id=type_id,
            kind="unexpected_division",
            qty=qty,
            occurred_at=now,
            flagged=True,
        )
        flagged += 1
    return flagged


async def get_wallet_division(
    session: AsyncSession, *, corporation_eve_id: int
) -> int | None:
    """The configured buyback wallet division (ADR-0045); None = sell-side off.
    Gated: the accounting entitlement is required (ADR-0042)."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    config = await config_repo.get_config(session, corp.id)
    return config.wallet_division if config else None


async def set_wallet_division(
    session: AsyncSession, *, corporation_eve_id: int, division: int | None
) -> int | None:
    """Point the sell side at the wallet division buyback sales pay into, or clear
    it (None) to switch ingestion off. Owns the commit; gated."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    record = await config_repo.set_wallet_division(
        session, corporation_id=corp.id, division=division
    )
    await session.commit()
    return record.wallet_division if record else None


def _is_outgoing_sale(
    contract: CorporationContract, corporation_eve_id: int
) -> bool:
    """Whether a contract is an OUTGOING sale (ADR-0045, #157): the corp issued it
    on its own behalf, it finished, and the buyer paid. Zero-price finished
    contracts (giveaways / internal moves) deliberately don't qualify."""
    return (
        contract.issuer_corporation_id == corporation_eve_id
        and contract.for_corporation
        and contract.status in ("finished", "finished_issuer", "finished_contractor")
        and contract.price > 0
    )


async def record_outgoing_contract_sales(
    session: AsyncSession,
    esi: EsiClient,
    *,
    corporation_uuid: uuid.UUID,
    corporation_eve_id: int,
    contracts: list[CorporationContract],
    access_token: str,
    now: datetime,
) -> int:
    """Record finished OUTGOING corp contracts as sales (ADR-0045, #157): contracts
    the corp itself issued on its own behalf (`issuer_corporation_id` == the corp +
    `for_corporation`), finished, with a price — the buyer paid `price` for the
    included items. Incoming buyback contracts (member-issued) never match the
    filter and are untouched.

    The contract's single price spans its items, so proceeds are allocated across
    the types pro-rata by market value at the corp's default hub (the ADR-0047
    allocator — exact to the cent), then each type FIFO-consumes lots at the
    contract's start location, with the shared no-lot fallback. Zero-price finished
    contracts (giveaways / internal moves) are deliberately NOT sales — the hangar
    check surfaces the departed stock for a human instead. `contract_id` is the
    idempotency key. Gated by the entitlement (skips, not raises — this rides the
    watcher, which serves unentitled corps too). Does not commit; the watcher owns
    the UoW."""
    if not await entitlements_app.corp_has_entitlement(
        session, corporation_id=corporation_uuid, feature="accounting", now=now
    ):
        return 0
    outgoing = [c for c in contracts if _is_outgoing_sale(c, corporation_eve_id)]
    if not outgoing:
        return 0
    seen = await sales_repo.external_refs_for_channel(
        session, corporation_id=corporation_uuid, channel="contract"
    )
    recorded = 0
    for contract in outgoing:
        if contract.contract_id in seen:
            continue
        if await _record_one_outgoing_contract(
            session,
            esi,
            corporation_uuid=corporation_uuid,
            corporation_eve_id=corporation_eve_id,
            contract=contract,
            access_token=access_token,
            now=now,
        ):
            recorded += 1
    return recorded


def _disposed_quantities(items: list[ContractItem]) -> dict[int, int]:
    """The items the contract hands over (the disposed stock), summed per type —
    included items only; whatever the buyer gives back is not a disposal."""
    disposed: dict[int, int] = {}
    for item in items:
        if item.is_included:
            disposed[item.type_id] = disposed.get(item.type_id, 0) + item.quantity
    return disposed


async def _record_one_outgoing_contract(
    session: AsyncSession,
    esi: EsiClient,
    *,
    corporation_uuid: uuid.UUID,
    corporation_eve_id: int,
    contract: CorporationContract,
    access_token: str,
    now: datetime,
) -> bool:
    """One outgoing contract → sale rows: fetch its items, split the price across
    them by market value (`domain/transformations.allocate_amount`), and push each
    type through the shared disposal path. False = nothing disposed (an ISK-only
    contract)."""
    items = await esi.get_corporation_contract_items(
        corporation_eve_id, contract.contract_id, access_token
    )
    disposed = _disposed_quantities(items)
    if not disposed:
        return False
    values = await transformations_app.split_off_values(
        session, corporation_uuid, sorted(disposed)
    )
    sold_at = contract.date_completed or now
    location_id = str(contract.start_location_id or "")
    for out in allocate_amount(contract.price, disposed, values):
        await consume_and_record_sale(
            session,
            corporation_uuid,
            type_id=out.type_id,
            qty=out.quantity,
            unit_proceeds=out.unit_cost,  # the allocator's per-unit share
            location_id=location_id,
            channel="contract",
            external_ref=contract.contract_id,
            sold_at=sold_at,
            now=now,
        )
    return True
