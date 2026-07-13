"""Background-job wiring (ADR-0034).

The interface-layer adapter for the scheduled market refresh: it opens a DB session,
constructs the plugin clients from app state + settings (the same wiring the request
dependencies do), and invokes the application use case. Like a router it holds no
business logic — it assembles dependencies and calls the use case — and it must never
raise: a scheduler job that propagates an exception logs noisily and can stop firing.
"""

import logging
from datetime import UTC, datetime

from fastapi import FastAPI

from app.application import (
    corp_contracts,
    corp_roster,
    entitlements,
    lots,
    market_refresh,
    payments,
    reconciliation,
)
from app.application.errors import CorpEsiTokenExpired, CorpEsiTokenMissing
from app.config import get_settings
from app.data.db import SessionLocal
from app.data.repositories import corp_esi_token as tokens_repo
from app.plugins.esi import CorporationAssetsForbidden, EsiClient
from app.plugins.esi_market import EsiMarketClient
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher

log = logging.getLogger(__name__)


async def run_market_refresh(app: FastAPI) -> None:
    """Scheduler entrypoint: refresh due non-Fuzzwork hub prices (ADR-0034). The use
    case already degrades per hub; this top-level guard keeps the recurring job alive
    across any unexpected failure."""
    settings = get_settings()
    esi_market = EsiMarketClient(app.state.http, app.state.esi_semaphore)
    sso = EveSsoClient(settings, app.state.http)
    cipher = TokenCipher(settings.token_encryption_key)
    try:
        async with SessionLocal() as session:
            await market_refresh.refresh_due_prices(
                session,
                esi_market=esi_market,
                sso=sso,
                cipher=cipher,
                cache=app.state.cache,
                settings=settings,
                now=datetime.now(UTC),
            )
    except Exception:  # noqa: BLE001 — a recurring job must survive any failure
        log.exception("market refresh job failed")


async def run_roster_refresh(app: FastAPI) -> None:
    """Scheduler entrypoint (ADR-0036): re-pull every token-holding corp's member roster
    so the manager-designation picker stays current without anyone clicking. Each corp runs
    in its own session and try/except — one corp's revoked or no-roster-access token never
    aborts the cycle — and the manual cooldown is bypassed."""
    settings = get_settings()
    esi = EsiClient(app.state.http)
    sso = EveSsoClient(settings, app.state.http)
    cipher = TokenCipher(settings.token_encryption_key)
    try:
        async with SessionLocal() as session:
            corp_eve_ids = await tokens_repo.list_corp_eve_ids_with_token(session)
    except Exception:  # noqa: BLE001 — a recurring job must survive any failure
        log.exception("roster refresh job: listing token-holding corps failed")
        return
    for corp_eve_id in corp_eve_ids:
        try:
            async with SessionLocal() as session:
                await corp_roster.refresh_roster(
                    session,
                    sso,
                    esi,
                    corporation_id=corp_eve_id,
                    cipher=cipher,
                    now=datetime.now(UTC),
                    enforce_cooldown=False,
                )
        except Exception as exc:  # noqa: BLE001 — one corp's failure must not abort the cycle
            # refresh_roster makes Bearer-token ESI calls; log `repr(exc)` (status/message,
            # not headers or a full traceback) so an access token can't reach the logs via
            # httpx's exception formatting (#75).
            log.warning("roster refresh failed for corp %s: %r", corp_eve_id, exc)


async def run_payments_reconcile(app: FastAPI) -> None:
    """Scheduler entrypoint (ADR-0042): read the operator's wallet journal and extend
    corp access for matching ISK payments. A quiet no-op until an app admin connects
    the operator wallet; the use case degrades on a revoked/scope-less grant."""
    settings = get_settings()
    esi = EsiClient(app.state.http)
    sso = EveSsoClient(settings, app.state.http)
    cipher = TokenCipher(settings.token_encryption_key)
    try:
        async with SessionLocal() as session:
            recorded = await payments.reconcile_payments(
                session, sso, esi, cipher=cipher, now=datetime.now(UTC)
            )
        if recorded:
            log.info("payment reconciliation recorded %d new payment(s)", recorded)
    except Exception as exc:  # noqa: BLE001 — a recurring job must survive any failure
        # Bearer-token ESI calls — log `repr(exc)` only, never `exc_info` (#75).
        log.warning("payment reconciliation failed: %r", exc)


async def run_accounting_write_downs(app: FastAPI) -> None:
    """Scheduler entrypoint (ADR-0043, #153): for every corp with an active accounting
    entitlement, write open lots down to market value where it fell below cost and book
    the loss. Pure DB work (cached prices only — no ESI, no tokens); each corp runs in
    its own session and try/except so one corp's failure never aborts the sweep."""
    settings = get_settings()
    try:
        async with SessionLocal() as session:
            access = await entitlements.list_corp_access(session, feature="accounting")
    except Exception:  # noqa: BLE001 — a recurring job must survive any failure
        log.exception("write-down sweep: listing entitled corps failed")
        return
    for corp in access:
        if not corp.active:
            continue
        try:
            async with SessionLocal() as session:
                written = await lots.apply_write_downs(
                    session,
                    corporation_eve_id=corp.corporation_id,
                    sales_tax_rate=settings.accounting_sales_tax_rate,
                    now=datetime.now(UTC),
                )
            if written:
                log.info(
                    "write-down sweep: %d lot(s) written down for corp %s",
                    written,
                    corp.corporation_id,
                )
        except Exception:  # noqa: BLE001 — one corp's failure must not abort the sweep
            log.exception("write-down sweep failed for corp %s", corp.corporation_id)


async def run_hangar_reconcile(app: FastAPI) -> None:
    """Scheduler entrypoint (ADR-0044, #155): for every corp with an active accounting
    entitlement, reconcile the marked buyback hangars against the ledger. A corp with
    no token, an expired grant, or a grant lacking the assets scope/role is skipped
    quietly WITHOUT flagging the token failed (the ADR-0037 pattern); each corp runs
    in its own session and try/except."""
    settings = get_settings()
    esi = EsiClient(app.state.http)
    sso = EveSsoClient(settings, app.state.http)
    cipher = TokenCipher(settings.token_encryption_key)
    try:
        async with SessionLocal() as session:
            access = await entitlements.list_corp_access(session, feature="accounting")
    except Exception:  # noqa: BLE001 — a recurring job must survive any failure
        log.exception("hangar reconcile: listing entitled corps failed")
        return
    for corp in access:
        if not corp.active:
            continue
        try:
            async with SessionLocal() as session:
                await reconciliation.reconcile_hangars(
                    session,
                    sso,
                    esi,
                    corporation_eve_id=corp.corporation_id,
                    cipher=cipher,
                    excess_flag_isk=settings.accounting_excess_flag_isk,
                    now=datetime.now(UTC),
                )
        except (CorpEsiTokenMissing, CorpEsiTokenExpired, CorporationAssetsForbidden):
            # No usable token or no assets scope/role — a reconnect problem the
            # Config page surfaces, not a job failure (#68 nuance).
            log.info(
                "hangar reconcile skipped for corp %s (token/scope unavailable)",
                corp.corporation_id,
            )
        except Exception as exc:  # noqa: BLE001 — one corp must not abort the sweep
            # Bearer-token ESI calls — log `repr(exc)` only, never `exc_info` (#75).
            log.warning(
                "hangar reconcile failed for corp %s: %r", corp.corporation_id, exc
            )


async def run_contracts_refresh(app: FastAPI) -> None:
    """Scheduler entrypoint (ADR-0037): poll every token-holding corp's EVE contracts and
    update matched-contract status on appraisals. Each corp runs in its own session and
    try/except so one corp's revoked/scope-less token never aborts the cycle."""
    settings = get_settings()
    esi = EsiClient(app.state.http)
    sso = EveSsoClient(settings, app.state.http)
    cipher = TokenCipher(settings.token_encryption_key)
    try:
        async with SessionLocal() as session:
            corp_eve_ids = await tokens_repo.list_corp_eve_ids_with_token(session)
    except Exception:  # noqa: BLE001 — a recurring job must survive any failure
        log.exception("contracts refresh job: listing token-holding corps failed")
        return
    for corp_eve_id in corp_eve_ids:
        try:
            async with SessionLocal() as session:
                await corp_contracts.refresh_contracts(
                    session,
                    sso,
                    esi,
                    corporation_id=corp_eve_id,
                    cipher=cipher,
                    now=datetime.now(UTC),
                )
        except Exception as exc:  # noqa: BLE001 — one corp's failure must not abort the cycle
            # Bearer-token ESI calls — log `repr(exc)` only, never `exc_info` (#75).
            log.warning("contracts refresh failed for corp %s: %r", corp_eve_id, exc)
