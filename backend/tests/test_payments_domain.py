"""ADR-0042: pure payment-reconciliation rules — reference parsing, payment
detection, period math, and expiry extension."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.payments import (
    extend_expiry,
    is_incoming_payment,
    parse_payment_reference,
    payment_reference,
    periods_for,
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
OPERATOR = 90000090


def test_reference_round_trips():
    assert payment_reference(98000001) == "BB-98000001"
    assert parse_payment_reference("BB-98000001") == 98000001


def test_reference_found_inside_donation_boilerplate():
    # EVE wraps player-donation reasons; the reference is searched, not matched.
    assert parse_payment_reference("DESC: for buyback access BB-98000001 thanks") == 98000001
    assert parse_payment_reference("bb-98000001") == 98000001  # case-insensitive


def test_reference_absent_or_malformed():
    assert parse_payment_reference(None) is None
    assert parse_payment_reference("") is None
    assert parse_payment_reference("thanks for the buyback") is None
    assert parse_payment_reference("BB-") is None


def test_incoming_payment_detection():
    ok = dict(amount=Decimal(100), second_party_id=OPERATOR, operator_character_id=OPERATOR)
    assert is_incoming_payment(ref_type="player_donation", **ok) is True
    assert is_incoming_payment(ref_type="corporation_account_withdrawal", **ok) is True
    # Market activity, taxes, and outgoing ISK are never payments.
    assert is_incoming_payment(ref_type="market_transaction", **ok) is False
    assert (
        is_incoming_payment(
            ref_type="player_donation",
            amount=Decimal(-100),  # the operator sending ISK out
            second_party_id=12345,
            operator_character_id=OPERATOR,
        )
        is False
    )
    assert (
        is_incoming_payment(
            ref_type="player_donation",
            amount=Decimal(100),
            second_party_id=12345,  # addressed to someone else
            operator_character_id=OPERATOR,
        )
        is False
    )


def test_periods_floor():
    price = 250_000_000
    assert periods_for(Decimal(250_000_000), price) == 1
    assert periods_for(Decimal(500_000_000), price) == 2
    assert periods_for(Decimal(749_999_999), price) == 2
    assert periods_for(Decimal(249_999_999), price) == 0  # under one period
    assert periods_for(Decimal(250_000_000), 0) == 0  # unpriced → never auto-match


def test_extend_stacks_on_active_access():
    current = NOW + timedelta(days=10)
    extended = extend_expiry(current, now=NOW, periods=1, period_days=30)
    assert extended == current + timedelta(days=30)


def test_extend_restarts_after_lapse():
    lapsed = NOW - timedelta(days=5)
    assert extend_expiry(lapsed, now=NOW, periods=2, period_days=30) == NOW + timedelta(days=60)


def test_extend_from_no_access():
    assert extend_expiry(None, now=NOW, periods=1, period_days=30) == NOW + timedelta(days=30)
