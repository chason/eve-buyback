"""ADR-0042: per-corp entitlements for paid features — the domain predicate, the
repository round-trip, the application-layer gate, and its HTTP mapping."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.application import entitlements as entitlements_app
from app.application.errors import EntitlementRequired
from app.data.db import SessionLocal
from app.data.repositories import corporations as corporations_repo
from app.data.repositories import entitlements as entitlements_repo
from app.domain.entitlements import entitlement_active
from app.interface.errors import _STATUS

NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)


# --- domain predicate -----------------------------------------------------------


def test_null_expiry_is_perpetual():
    assert entitlement_active(None, NOW) is True


def test_future_expiry_is_active():
    assert entitlement_active(NOW + timedelta(days=30), NOW) is True


def test_past_expiry_is_inactive():
    assert entitlement_active(NOW - timedelta(seconds=1), NOW) is False


def test_exact_expiry_moment_is_inactive():
    assert entitlement_active(NOW, NOW) is False


# --- repository + application gate ----------------------------------------------


async def _corp(session) -> uuid.UUID:
    corp = await corporations_repo.create_corporation(
        session,
        eve_corporation_id=98000001,
        name="Test Corp",
        ceo_character_id=1,
        registered_by_character_id=1,
    )
    return corp.id


async def test_grant_get_revoke_round_trip():
    async with SessionLocal() as session:
        corp_id = await _corp(session)

        assert (
            await entitlements_repo.get(
                session, corporation_id=corp_id, feature="accounting"
            )
            is None
        )

        record = await entitlements_repo.upsert(
            session,
            corporation_id=corp_id,
            feature="accounting",
            source="admin",
            expires_at=None,
            granted_by_character_id=42,
        )
        assert record.source == "admin"
        assert record.expires_at is None  # perpetual
        assert record.granted_by_character_id == 42

        # Extend/re-grant updates the one row in place (payment sets a real expiry).
        extended = await entitlements_repo.upsert(
            session,
            corporation_id=corp_id,
            feature="accounting",
            source="payment",
            expires_at=NOW + timedelta(days=30),
        )
        assert extended.source == "payment"
        assert extended.expires_at is not None
        assert extended.granted_by_character_id is None
        assert extended.granted_at == record.granted_at  # original grant time kept

        assert (
            await entitlements_repo.delete(
                session, corporation_id=corp_id, feature="accounting"
            )
            is True
        )
        assert (
            await entitlements_repo.delete(
                session, corporation_id=corp_id, feature="accounting"
            )
            is False
        )


async def test_gate_missing_entitlement_raises():
    async with SessionLocal() as session:
        corp_id = await _corp(session)
        assert (
            await entitlements_app.corp_has_entitlement(
                session, corporation_id=corp_id, feature="accounting"
            )
            is False
        )
        with pytest.raises(EntitlementRequired):
            await entitlements_app.require_entitlement(
                session, corporation_id=corp_id, feature="accounting"
            )


async def test_gate_active_entitlement_passes():
    async with SessionLocal() as session:
        corp_id = await _corp(session)
        await entitlements_repo.upsert(
            session,
            corporation_id=corp_id,
            feature="accounting",
            source="admin",
            expires_at=None,
        )
        assert (
            await entitlements_app.corp_has_entitlement(
                session, corporation_id=corp_id, feature="accounting"
            )
            is True
        )
        # Does not raise.
        await entitlements_app.require_entitlement(
            session, corporation_id=corp_id, feature="accounting"
        )


async def test_gate_expired_entitlement_raises():
    async with SessionLocal() as session:
        corp_id = await _corp(session)
        await entitlements_repo.upsert(
            session,
            corporation_id=corp_id,
            feature="accounting",
            source="payment",
            expires_at=NOW - timedelta(days=1),
        )
        with pytest.raises(EntitlementRequired):
            await entitlements_app.require_entitlement(
                session, corporation_id=corp_id, feature="accounting", now=NOW
            )


# --- HTTP mapping ---------------------------------------------------------------


def test_entitlement_required_maps_to_402():
    assert _STATUS[EntitlementRequired] == 402
