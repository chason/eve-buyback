"""Buyback-hangar use cases (ADR-0044, #154): configure which corp hangar divisions
count as "the buyback hangar", and read their physical contents via the Corp ESI
token. The reconciliation itself (#155) consumes `fetch_hangar_counts`.

The config actions are gated by the accounting entitlement (ADR-0042); the hangar
picker offers only the corp's existing drop-off locations (ADR-0030) — the buyback
hangar is where members deliver, so the constraint is natural, and it keeps the UI a
select rather than a second location-search surface.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.application import corp_esi_token as corp_esi_token_app
from app.application import entitlements as entitlements_app
from app.application.corporations import get_registered_corporation
from app.application.errors import HangarLocationUnknown
from app.data.records import BuybackHangarRecord
from app.data.repositories import buyback_locations as locations_repo
from app.data.repositories import hangars as hangars_repo
from app.domain.hangar import AssetStack, HangarKey, hangar_counts
from app.plugins.esi import EsiClient
from app.plugins.sso import EveSsoClient
from app.plugins.token_cipher import TokenCipher

log = logging.getLogger(__name__)


async def list_hangars(
    session: AsyncSession, *, corporation_eve_id: int
) -> list[BuybackHangarRecord]:
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    return await hangars_repo.list_for_corp(session, corp.id)


async def add_hangar(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    location_id: str,
    division: int,
) -> BuybackHangarRecord:
    """Mark one hangar division as buyback stock. The location must be one of the
    corp's drop-off locations; its display name is snapshotted from there. Idempotent:
    re-adding an existing hangar returns it unchanged."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    existing = await hangars_repo.get(
        session, corporation_id=corp.id, location_id=location_id, division=division
    )
    if existing is not None:
        return existing
    location = await locations_repo.get(session, corp.id, location_id)
    if location is None:
        raise HangarLocationUnknown()
    record = await hangars_repo.add(
        session,
        corporation_id=corp.id,
        location_id=location_id,
        location_name=location.name,
        division=division,
    )
    await session.commit()
    return record


async def remove_hangar(
    session: AsyncSession,
    *,
    corporation_eve_id: int,
    location_id: str,
    division: int,
) -> None:
    """Idempotent: removing a hangar that isn't configured is a no-op (the end state
    already holds)."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    await entitlements_app.require_entitlement(
        session, corporation_id=corp.id, feature="accounting"
    )
    await hangars_repo.delete_for_corp(
        session, corporation_id=corp.id, location_id=location_id, division=division
    )
    await session.commit()


async def fetch_hangar_counts(
    session: AsyncSession,
    sso: EveSsoClient,
    esi: EsiClient,
    *,
    corporation_eve_id: int,
    cipher: TokenCipher,
) -> dict[tuple[str, int], int]:
    """The physical `(location_id, type_id) → qty` count across the corp's configured
    buyback hangars (ADR-0044) — the stock-take the reconciliation (#155) compares the
    ledger against. Empty when no hangars are configured (no ESI call is made).

    Raises the token/scope exceptions as-is (`CorpEsiTokenMissing`/`Expired`,
    `CorporationAssetsForbidden`): the caller decides how to degrade — the sync job
    logs and skips WITHOUT flagging the token failed (a missing scope/role is a
    reconnect problem, not a refresh failure — the ADR-0037 pattern)."""
    corp = await get_registered_corporation(session, corporation_eve_id)
    hangars = await hangars_repo.list_for_corp(session, corp.id)
    if not hangars:
        return {}
    access_token = await corp_esi_token_app.get_corp_esi_access_token(
        session, sso, corporation_uuid=corp.id, cipher=cipher
    )
    assets = await esi.get_corporation_assets(corporation_eve_id, access_token)
    return hangar_counts(
        [
            AssetStack(
                item_id=a.item_id,
                type_id=a.type_id,
                quantity=a.quantity,
                location_id=a.location_id,
                location_flag=a.location_flag,
            )
            for a in assets
        ],
        [HangarKey(location_id=h.location_id, division=h.division) for h in hangars],
    )
