"""Hangar reconciliation use cases (ADR-0044, #155): compare the physical buyback
hangar against the ledger's idle stock, book unexplained excess as deemed-cost lots,
flag shortfalls, and log every change. The first run over a corp's stock doubles as
the opening-balance importer.

Valuation discipline (ADR-0043/0044): matched stock is never touched — only the
unexplained delta gets a deemed cost, fixed at creation and never re-priced. Deemed
cost is the corp's own buyback answer for the type (resolved rule × cached market
value at the corp's default hub), falling back to 90% of Jita buy. Two documented
simplifications, both acceptable for an always-flagged estimate: a rule's per-rule
hub override is ignored (the corp default hub prices everything), and an ore rule's
reprocess pricing uses the ore's own market value rather than its mineral yield.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.application import entitlements as entitlements_app
from app.application import hangar as hangar_app
from app.application.corporations import get_registered_corporation
from app.application.errors import (
    HangarReadUnavailable,
    MoveSuggestionNotFound,
    MoveSuggestionNotPending,
)
from app.data.records import MoveSuggestionRecord, ReconciliationEventRecord
from app.data.repositories import buyback_config as config_repo
from app.data.repositories import hangars as hangars_repo
from app.data.repositories import lots as lots_repo
from app.data.repositories import market_orders as orders_repo
from app.data.repositories import move_suggestions as moves_repo
from app.data.repositories import prices as prices_repo
from app.data.repositories import pricing_rules as rules_repo
from app.data.repositories import reconciliation as recon_repo
from app.data.repositories import sde as sde_repo
from app.domain import pricing as pricing_domain
from app.domain.moves import MovePair, match_move_pairs
from app.domain.reconciliation import Delta, reconcile
from app.domain.transformations import match_reprocess_hints
from app.plugins.esi import CorporationAssetsForbidden, EsiClient
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher

log = logging.getLogger(__name__)

_JITA = "60003760"
_JITA_FALLBACK_PERCENTAGE = Decimal("90")


class HangarCheckResult(BaseModel):
    """What one sync changed: lots booked for excess, and differences flagged for a
    look (shortfalls + unpriceable or unusually large excess)."""

    lots_added: int
    flagged: int


@dataclass(frozen=True)
class ReconciliationEventView:
    """A log entry enriched for display: names resolved so the UI stays plain."""

    record: ReconciliationEventRecord
    type_name: str | None
    location_name: str | None


@dataclass(frozen=True)
class MoveSuggestionView:
    """A pending "looks like a move" pairing enriched for display (ADR-0049):
    names resolved so the UI stays plain."""

    record: MoveSuggestionRecord
    type_name: str | None
    origin_name: str | None
    destination_name: str | None


async def reconcile_hangars(
    session: AsyncSession,
    sso: EveSsoClient,
    esi: EsiClient,
    *,
    corporation_eve_id: int,
    cipher: TokenCipher,
    excess_flag_isk: Decimal | int,
    now: datetime | None = None,
) -> HangarCheckResult:
    """One reconciliation pass for one corp (ADR-0044). Idempotent on the delta: a
    booked excess raises the ledger's expected stock by exactly the delta, so the
    next pass sees zero; an unchanged shortfall is not re-logged. Owns the commit.

    Raises the token/scope exceptions (`CorpEsiTokenMissing`/`Expired`,
    `CorporationAssetsForbidden`) as-is — the job logs and skips, the manual check
    maps them to a "reconnect" answer."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    now = now or datetime.now(UTC)

    counted = await hangar_app.fetch_hangar_counts(
        session, sso, esi, corporation_eve_id=corporation_eve_id, cipher=cipher
    )
    hangars = await hangars_repo.list_for_corp(session, corp.id)
    if not hangars:
        return HangarCheckResult(lots_added=0, flagged=0)

    locations = sorted({h.location_id for h in hangars})
    expected = await lots_repo.idle_by_location_type(
        session, corporation_id=corp.id, location_ids=locations
    )
    # Units in sell-order escrow have physically left the hangar (ADR-0043's
    # qty_idle; the snapshot comes from the sales ingestion, ADR-0045) — the hangar
    # not holding them is correct, not a shortfall.
    listed = await orders_repo.listed_by_location_type(
        session, corporation_id=corp.id
    )
    for slot, qty in listed.items():
        if slot in expected:
            expected[slot] = max(0, expected[slot] - qty)
    deltas = reconcile(counted, expected)
    if not deltas:
        # A clean hangar still invalidates any pending move pair: no deltas
        # means no standing shortfall, so nothing is left for a pair to
        # decorate — withdraw it (ADR-0049, #202) before returning.
        await _withdraw_invalid_suggestions(
            session, corp.id, shortfall_events={}, shortfall_slots=set(), now=now
        )
        await session.commit()
        return HangarCheckResult(lots_added=0, flagged=0)

    # A reprocessable-type shortfall + yield-consistent materials excess looks like
    # an unrecorded reprocess (ADR-0047): suggest it instead of flagging a loss and
    # booking re-estimated materials — that would sever the cost lineage the manual
    # record action exists to preserve. Matched slots skip the normal paths for
    # this pass; the suggestion repeats until recorded (or the pattern changes).
    hints = await _reprocess_hints(session, deltas)
    hinted_sources = {(h.location_id, h.type_id) for h in hints}
    hinted_materials = {
        (h.location_id, tid) for h in hints for tid in h.material_type_ids
    }

    deemed = await deemed_unit_costs(
        session, corp.id, sorted({d.type_id for d in deltas if d.kind == "excess"})
    )
    latest = await recon_repo.latest_by_slot(session, corporation_id=corp.id)

    lots_added = 0
    flagged = 0
    # The reconciliation artifacts this pass stands behind, by slot — a move
    # pair (ADR-0049) decorates exactly these, so their ids are collected as the
    # default treatment books them.
    excess_lots: dict[tuple[str, int], uuid.UUID] = {}
    shortfall_events: dict[tuple[str, int], uuid.UUID] = {}
    for hint in hints:
        hint_delta = Delta(
            location_id=hint.location_id,
            type_id=hint.type_id,
            kind="reprocess_hint",
            qty=hint.qty,
        )
        if _already_logged(latest, hint_delta, lot_id_present=None):
            continue
        await recon_repo.add_event(
            session,
            corporation_id=corp.id,
            location_id=hint.location_id,
            type_id=hint.type_id,
            kind="reprocess_hint",
            qty=hint.qty,
            occurred_at=now,
            flagged=True,
        )
        flagged += 1
    for delta in deltas:
        slot = (delta.location_id, delta.type_id)
        if delta.kind == "shortfall" and slot in hinted_sources:
            continue  # explained by the suggestion, not a loss to flag
        if delta.kind == "excess" and slot in hinted_materials:
            continue  # would be re-estimated materials; the reprocess records them
        if delta.kind == "excess":
            unit_cost = deemed.get(delta.type_id)
            if unit_cost is None:
                if _already_logged(latest, delta, lot_id_present=False):
                    continue  # still unpriceable, still the same delta — no re-log
                await recon_repo.add_event(
                    session,
                    corporation_id=corp.id,
                    location_id=delta.location_id,
                    type_id=delta.type_id,
                    kind="excess",
                    qty=delta.qty,
                    occurred_at=now,
                    flagged=True,
                    note="No market price available to value it; will retry",
                )
                flagged += 1
                continue
            lot = await lots_repo.create_lot(
                session,
                corporation_id=corp.id,
                item_type_id=delta.type_id,
                qty=delta.qty,
                unit_purchase_cost=unit_cost,
                acquired_at=now,
                source="opening_balance",
                cost_is_estimated=True,
                location_id=delta.location_id,
                notes="Found in the hangar check (ADR-0044)",
            )
            is_large = delta.qty * unit_cost >= Decimal(excess_flag_isk)
            await recon_repo.add_event(
                session,
                corporation_id=corp.id,
                location_id=delta.location_id,
                type_id=delta.type_id,
                kind="excess",
                qty=delta.qty,
                occurred_at=now,
                unit_cost=unit_cost,
                lot_id=lot.id,
                flagged=is_large,
            )
            excess_lots[slot] = lot.id
            lots_added += 1
            if is_large:
                flagged += 1
        else:
            if _already_logged(latest, delta, lot_id_present=None):
                # The same shortfall was already flagged — no daily spam. The
                # standing flag still anchors a move pair below (the freighter
                # may only now have arrived at the other hangar).
                shortfall_events[slot] = latest[slot].id
                continue
            event = await recon_repo.add_event(
                session,
                corporation_id=corp.id,
                location_id=delta.location_id,
                type_id=delta.type_id,
                kind="shortfall",
                qty=delta.qty,
                occurred_at=now,
                flagged=True,
            )
            shortfall_events[slot] = event.id
            flagged += 1

    # A pending pair whose ends stopped holding — the excess evaporated, or the
    # shortfall flag was resolved some other way — is withdrawn before new
    # pairing runs (ADR-0049, #202), so a superseded suggestion never lingers
    # next to the fresh one that replaces it.
    shortfall_slots = {
        (d.location_id, d.type_id) for d in deltas if d.kind == "shortfall"
    }
    await _withdraw_invalid_suggestions(
        session,
        corp.id,
        shortfall_events=shortfall_events,
        shortfall_slots=shortfall_slots,
        now=now,
    )

    # A same-type shortfall at one hangar + excess at another looks like a haul
    # nobody recorded (ADR-0049, #200). Suggest-only — the heart of this slice:
    # the defaults above are already booked (the shortfall flag stands, the
    # deemed-cost lot exists), and the suggestion merely decorates them so a
    # human can act later. Excess that couldn't be priced has no lot to decorate
    # yet — the pair proposes on the pass that books it. A still-pending pair is
    # never duplicated by a later sync, and a pattern a human already dismissed
    # (same standing flag, same quantity) is never re-asked (#202).
    for pair in _move_pairs(deltas, hinted_sources, hinted_materials):
        event_id = shortfall_events.get((pair.origin_location_id, pair.type_id))
        lot_id = excess_lots.get((pair.destination_location_id, pair.type_id))
        if event_id is None or lot_id is None:
            continue
        already = await moves_repo.pending_exists(
            session,
            corporation_id=corp.id,
            shortfall_event_id=event_id,
            excess_lot_id=lot_id,
        )
        if already:
            continue
        dismissed = await moves_repo.dismissed_exists(
            session,
            corporation_id=corp.id,
            shortfall_event_id=event_id,
            qty=pair.qty,
        )
        if dismissed:
            continue
        await moves_repo.add(
            session,
            corporation_id=corp.id,
            type_id=pair.type_id,
            origin_location_id=pair.origin_location_id,
            destination_location_id=pair.destination_location_id,
            qty=pair.qty,
            shortfall_event_id=event_id,
            excess_lot_id=lot_id,
        )

    await session.commit()
    if lots_added or flagged:
        log.info(
            "hangar reconcile for corp %s: %d lot(s) booked, %d flagged",
            corporation_eve_id,
            lots_added,
            flagged,
        )
    return HangarCheckResult(lots_added=lots_added, flagged=flagged)


async def _reprocess_hints(session: AsyncSession, deltas: list[Delta]):
    """Feed the shortfall/excess pattern to the pure matcher (ADR-0047). Yield data
    and portion sizes come from the SDE — absent types simply never match."""
    shortfalls = [
        (d.location_id, d.type_id, d.qty) for d in deltas if d.kind == "shortfall"
    ]
    if not shortfalls:
        return []
    excesses = [
        (d.location_id, d.type_id, d.qty) for d in deltas if d.kind == "excess"
    ]
    if not excesses:
        return []
    source_type_ids = sorted({tid for _, tid, _ in shortfalls})
    materials = await sde_repo.get_type_materials(session, source_type_ids)
    types = await sde_repo.get_types(session, source_type_ids)
    return match_reprocess_hints(
        shortfalls,
        excesses,
        materials,
        {tid: t.portion_size for tid, t in types.items()},
    )


def _move_pairs(
    deltas: list[Delta],
    hinted_sources: set[tuple[str, int]],
    hinted_materials: set[tuple[str, int]],
) -> list[MovePair]:
    """Feed the default-treated deltas to the pure pairer (ADR-0049). Slots the
    reprocess suggestion claimed are excluded: they skipped the default
    treatment, so there is no flag or lot for a move pair to decorate."""
    shortfalls: list[tuple[str, int, int]] = []
    excesses: list[tuple[str, int, int]] = []
    for delta in deltas:
        slot = (delta.location_id, delta.type_id)
        if delta.kind == "shortfall" and slot not in hinted_sources:
            shortfalls.append((delta.location_id, delta.type_id, delta.qty))
        elif delta.kind == "excess" and slot not in hinted_materials:
            excesses.append((delta.location_id, delta.type_id, delta.qty))
    if not shortfalls or not excesses:
        return []
    return match_move_pairs(shortfalls, excesses)


async def _withdraw_invalid_suggestions(
    session: AsyncSession,
    corporation_id: uuid.UUID,
    *,
    shortfall_events: dict[tuple[str, int], uuid.UUID],
    shortfall_slots: set[tuple[str, int]],
    now: datetime,
) -> None:
    """Withdraw pending suggestions whose pair no longer holds (ADR-0049, #202)
    against the fresh sync: `shortfall_events` maps each still-short slot to its
    standing flag event, `shortfall_slots` is every slot the fresh pass found
    short. A withdrawal is bookkeeping, not a decision — it's logged with its
    own kind so it never reads like a dismissal, and it never suppresses a
    future pairing the way a dismissal does."""
    pending = await moves_repo.list_pending(session, corporation_id=corporation_id)
    for s in pending:
        reason = _withdraw_reason(s, shortfall_events, shortfall_slots)
        if reason is None:
            continue
        await moves_repo.set_status(
            session, suggestion_id=s.id, status="withdrawn"
        )
        await recon_repo.add_event(
            session,
            corporation_id=corporation_id,
            location_id=s.origin_location_id,
            type_id=s.type_id,
            kind="move_withdrawn",
            qty=s.qty,
            occurred_at=now,
            note=reason,
        )


def _withdraw_reason(
    s: MoveSuggestionRecord,
    shortfall_events: dict[tuple[str, int], uuid.UUID],
    shortfall_slots: set[tuple[str, int]],
) -> str | None:
    """Why a pending pair stopped holding, or None while it still does. The
    excess end fails when the deemed lot is gone or the destination now counts
    SHORT of the books (the found stock left again); the shortfall end fails
    when the flag this pair decorates is no longer the slot's standing anchor —
    resolved some other way, or superseded by a changed magnitude."""
    if s.excess_lot_id is None:
        return "The found stock it pointed at is no longer on the books"
    if (s.destination_location_id, s.type_id) in shortfall_slots:
        return "The found stock is no longer there"
    if shortfall_events.get((s.origin_location_id, s.type_id)) != s.shortfall_event_id:
        return "The missing-stock flag was resolved another way"
    return None


def _already_logged(
    latest: dict[tuple[str, int], ReconciliationEventRecord],
    delta: Delta,
    *,
    lot_id_present: bool | None,
) -> bool:
    """Whether the slot's most recent log entry already says exactly this — same
    kind, same magnitude (and for unpriceable excess, still no lot). A changed
    magnitude logs again: the situation moved, a human should see it."""
    last = latest.get((delta.location_id, delta.type_id))
    if last is None or last.kind != delta.kind or last.qty != delta.qty:
        return False
    if lot_id_present is False and last.lot_id is not None:
        return False
    return True


async def deemed_unit_costs(
    session: AsyncSession, corporation_id: uuid.UUID, type_ids: list[int]
) -> dict[int, Decimal]:
    """The deemed cost per unit for discovered stock (ADR-0044): what the corp's own
    buyback would pay for it today — the resolved rule's percentage × the cached
    market value at the corp's default hub — falling back to 90% of Jita buy when the
    type has no rule-priced answer. Types absent from the result can't be valued yet
    (no cached price anywhere); the caller flags them instead of inventing a cost."""
    if not type_ids:
        return {}
    config = await config_repo.get_config(session, corporation_id)
    if config is None:
        return {}

    types = await sde_repo.get_types(session, type_ids)
    parent_of = {
        g.market_group_id: g.parent_id
        for g in await sde_repo.list_market_groups(session)
    }
    rules = await rules_repo.list_rules(session, corporation_id)
    type_rules = {
        r.target_id: pricing_domain.RuleSpec(
            r.basis, r.percentage, r.reprocess, r.compressed_only, r.accepted
        )
        for r in rules
        if r.enabled and r.target_kind == "type"
    }
    group_rules = {
        r.target_id: pricing_domain.RuleSpec(
            r.basis, r.percentage, r.reprocess, r.compressed_only, r.accepted
        )
        for r in rules
        if r.enabled and r.target_kind == "market_group"
    }

    hub_prices = {
        p.type_id: p
        for p in await prices_repo.get_prices(
            session, hub_id=config.market_hub_id, type_ids=type_ids
        )
    }
    jita_prices = (
        hub_prices
        if config.market_hub_id == _JITA
        else {
            p.type_id: p
            for p in await prices_repo.get_prices(
                session, hub_id=_JITA, type_ids=type_ids
            )
        }
    )

    out: dict[int, Decimal] = {}
    for type_id in type_ids:
        sde_type = types.get(type_id)
        rule = pricing_domain.resolve_rule(
            type_id,
            sde_type.market_group_id if sde_type else None,
            type_rules=type_rules,
            group_rules=group_rules,
            parent_of=parent_of,
            default_basis=config.default_basis,
            default_percentage=config.default_percentage,
            default_accepted=config.default_accepted,
        )
        price = hub_prices.get(type_id)
        value = (
            pricing_domain.select_aggregate(
                _side(price, "buy", config.aggregate_field),
                _side(price, "sell", config.aggregate_field),
                rule.basis,
            )
            if price is not None and rule.accepted
            else None
        )
        if value is not None:
            out[type_id] = pricing_domain.unit_price(value, rule.percentage)
            continue
        # Fallback (ADR-0044): 90% of Jita buy — for blacklisted types and types
        # unpriced at the corp's hub.
        jita = jita_prices.get(type_id)
        jita_buy = _side(jita, "buy", "percentile") if jita is not None else None
        if jita_buy is not None:
            out[type_id] = pricing_domain.unit_price(
                jita_buy, _JITA_FALLBACK_PERCENTAGE
            )
    return out


def _side(price, side: str, field: str) -> Decimal | None:
    """One side's aggregate off a MarketPriceRecord, None when that side has no
    orders (mirrors the appraisal pricing's data-quality guard)."""
    if getattr(price, f"{side}_order_count") <= 0:
        return None
    return getattr(price, f"{side}_{field}")


async def list_events(
    session: AsyncSession, *, corporation_eve_id: int, limit: int = 50
) -> list[ReconciliationEventView]:
    """The recent reconciliation log for the "Needs a look" list, names resolved.
    Gated: the accounting entitlement is required (ADR-0042)."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    records = await recon_repo.list_for_corp(
        session, corporation_id=corp.id, limit=limit
    )
    types = await sde_repo.get_types(session, sorted({r.type_id for r in records}))
    hangar_names = {
        h.location_id: h.location_name
        for h in await hangars_repo.list_for_corp(session, corp.id)
    }
    return [
        ReconciliationEventView(
            record=r,
            type_name=types[r.type_id].name if r.type_id in types else None,
            location_name=hangar_names.get(r.location_id),
        )
        for r in records
    ]


async def list_move_suggestions(
    session: AsyncSession, *, corporation_eve_id: int
) -> list[MoveSuggestionView]:
    """The pending "looks like a move" pairings for the "Needs a look" area
    (ADR-0049, #200), names resolved — read-only in this slice. Gated: the
    accounting entitlement is required (ADR-0042)."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    records = await moves_repo.list_pending(session, corporation_id=corp.id)
    types = await sde_repo.get_types(session, sorted({r.type_id for r in records}))
    hangar_names = {
        h.location_id: h.location_name
        for h in await hangars_repo.list_for_corp(session, corp.id)
    }
    return [_suggestion_view(r, types, hangar_names) for r in records]


async def dismiss_move_suggestion(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    suggestion_id: uuid.UUID,
    dismissed_by_character_name: str,
    now: datetime | None = None,
) -> None:
    """"Not a move" (ADR-0049, #202): resolve the suggestion without converting
    anything — the ADR-0044 defaults stand exactly as booked (the shortfall flag
    keeps waiting for a human, the deemed-cost lot keeps its estimate), and the
    decision lands in the reconciliation log with who made it. The dismissed
    pattern is never re-suggested while the underlying flags are unchanged.
    Idempotent on an already-dismissed suggestion; a confirmed or withdrawn one
    is a conflict — there is nothing left for a dismissal to decide. Gated:
    the accounting entitlement is required (ADR-0042). Owns the commit."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    suggestion = await moves_repo.get_for_corp(
        session, corporation_id=corp.id, suggestion_id=suggestion_id
    )
    if suggestion is None:
        raise MoveSuggestionNotFound()
    if suggestion.status == "dismissed":
        return
    if suggestion.status != "pending":
        raise MoveSuggestionNotPending()
    await moves_repo.set_status(
        session, suggestion_id=suggestion.id, status="dismissed"
    )
    await recon_repo.add_event(
        session,
        corporation_id=corp.id,
        location_id=suggestion.origin_location_id,
        type_id=suggestion.type_id,
        kind="move_dismissed",
        qty=suggestion.qty,
        occurred_at=now or datetime.now(UTC),
        note=f"Not a move — dismissed by {dismissed_by_character_name}",
    )
    await session.commit()


def _suggestion_view(
    record: MoveSuggestionRecord, types: dict, hangar_names: dict[str, str]
) -> MoveSuggestionView:
    type_name = types[record.type_id].name if record.type_id in types else None
    return MoveSuggestionView(
        record=record,
        type_name=type_name,
        origin_name=hangar_names.get(record.origin_location_id),
        destination_name=hangar_names.get(record.destination_location_id),
    )


async def run_manual_check(
    session: AsyncSession,
    sso: EveSsoClient,
    esi: EsiClient,
    *,
    corporation_eve_id: int,
    cipher: TokenCipher,
    excess_flag_isk: Decimal | int,
) -> HangarCheckResult:
    """The Stock page's "Check the hangar now" button. Same pass as the background
    job, but a missing assets scope/role becomes a typed "reconnect" answer instead
    of a silent skip — a human is watching. Entitlement-gated."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    try:
        return await reconcile_hangars(
            session,
            sso,
            esi,
            corporation_eve_id=corporation_eve_id,
            cipher=cipher,
            excess_flag_isk=excess_flag_isk,
        )
    except CorporationAssetsForbidden:
        raise HangarReadUnavailable() from None
