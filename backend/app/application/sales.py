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

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.application import corp_esi_token as corp_esi_token_app
from app.application import entitlements as entitlements_app
from app.application import reconciliation as reconciliation_app
from app.application.corporations import get_registered_corporation
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import expenses as expenses_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import market_orders as orders_repo
from app.data.repositories import reconciliation as recon_repo
from app.data.repositories import sales as sales_repo
from app.domain.lots import LotConsumption, OpenLot, plan_fifo
from app.plugins.esi import CorpWalletTransaction, EsiClient
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher

log = logging.getLogger(__name__)

# Journal ref types the ingestion books (ADR-0045).
_TAX_REF = "transaction_tax"
_BROKER_REFS = frozenset({"brokers_fee", "market_provider_tax"})


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
        orders=[
            {
                "order_id": o.order_id,
                "type_id": o.type_id,
                "is_buy_order": o.is_buy_order,
                "price": o.price,
                "volume_remain": o.volume_remain,
                "volume_total": o.volume_total,
                "location_id": str(o.location_id),
                "wallet_division": o.wallet_division,
                "issued": o.issued,
            }
            for o in orders
        ],
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
        unmatched = await _record_fill(session, corp.id, tx, now=now)
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


async def _record_fill(
    session: AsyncSession,
    corporation_id: uuid.UUID,
    tx: CorpWalletTransaction,
    *,
    now: datetime,
) -> bool:
    """Turn one sell fill into FIFO-consuming sale rows (one per lot touched,
    ADR-0043). Stock the books didn't have — the no-lot sale (ADR-0045) — books a
    deemed-cost lot for the shortfall (`cost_is_estimated=TRUE`, so the estimate
    propagates into realized profit), consumes it, and flags the event in the
    reconciliation log. Returns True when that fallback fired."""
    location_id = str(tx.location_id)
    lots = await lots_repo.open_lots(
        session,
        corporation_id=corporation_id,
        item_type_id=tx.type_id,
        location_id=location_id,
    )
    plan = plan_fifo(
        [
            OpenLot(
                lot_id=lot.id,
                qty_remaining=lot.qty_remaining,
                acquired_at=lot.acquired_at,
            )
            for lot in lots
        ],
        tx.quantity,
    )
    consumptions = list(plan.consumptions)
    unmatched = plan.shortfall > 0
    if unmatched:
        # The same deemed-cost policy the hangar reconciliation uses (ADR-0044/0045).
        deemed = await reconciliation_app.deemed_unit_costs(
            session, corporation_id, [tx.type_id]
        )
        # No market evidence either → the sale's own proceeds are the only cost
        # signal left; deem at proceeds (zero estimated profit, never invented gain).
        unit_cost = deemed.get(tx.type_id, tx.unit_price)
        shortfall_lot = await lots_repo.create_lot(
            session,
            corporation_id=corporation_id,
            item_type_id=tx.type_id,
            qty=plan.shortfall,
            unit_purchase_cost=unit_cost,
            acquired_at=tx.date,
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
            type_id=tx.type_id,
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
            unit_proceeds=tx.unit_price,
            channel="market",
            source="esi",
            sold_at=tx.date,
            external_ref=tx.transaction_id,
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
