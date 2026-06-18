"""Pure contract-matching rules (ADR-0037): lifecycle-status derivation and the
items/price/location match check. No I/O — see test_corp_contracts.py for the use case."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.contracts import contract_matches, derive_lifecycle_status

NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
PAST = NOW - timedelta(days=1)
FUTURE = NOW + timedelta(days=1)


# --- derive_lifecycle_status ---


def test_outstanding_is_in_progress():
    assert (
        derive_lifecycle_status("outstanding", date_expired=FUTURE, now=NOW)
        == "in_progress"
    )


def test_in_progress_stays_in_progress():
    assert (
        derive_lifecycle_status("in_progress", date_expired=None, now=NOW)
        == "in_progress"
    )


def test_outstanding_past_expiry_is_expired():
    # ESI has no "expired" status; we derive it from date_expired < now.
    assert (
        derive_lifecycle_status("outstanding", date_expired=PAST, now=NOW) == "expired"
    )


def test_finished_variants_all_map_to_completed():
    for s in ("finished", "finished_issuer", "finished_contractor"):
        assert derive_lifecycle_status(s, date_expired=None, now=NOW) == "completed"


def test_voided_statuses_pass_through():
    for s in ("cancelled", "rejected", "failed"):
        assert derive_lifecycle_status(s, date_expired=None, now=NOW) == s


def test_gone_statuses_drop_the_link():
    for s in ("deleted", "reversed"):
        assert derive_lifecycle_status(s, date_expired=None, now=NOW) is None


def test_unknown_status_is_untracked():
    assert derive_lifecycle_status("nonsense", date_expired=None, now=NOW) is None


# --- contract_matches ---

# A canonical matching contract: 1.23M ISK, one structure, exactly the accepted items.
_MATCH = dict(
    price=Decimal("1230000.00"),
    start_location_id=1035000000001,
    items={34: 100, 35: 5},
    accepted_total=Decimal("1230000.00"),
    delivery_location_id="1035000000001",
    accepted_items={34: 100, 35: 5},
)


def test_exact_match_passes():
    assert contract_matches(**_MATCH) is True


def test_wrong_price_fails():
    assert contract_matches(**{**_MATCH, "price": Decimal("1.00")}) is False


def test_wrong_location_fails():
    assert contract_matches(**{**_MATCH, "start_location_id": 60003760}) is False


def test_missing_location_fails():
    # No delivery location recorded → can't confirm the match.
    assert contract_matches(**{**_MATCH, "delivery_location_id": None}) is False
    assert contract_matches(**{**_MATCH, "start_location_id": None}) is False


def test_extra_item_fails():
    assert contract_matches(**{**_MATCH, "items": {34: 100, 35: 5, 36: 1}}) is False


def test_missing_item_fails():
    assert contract_matches(**{**_MATCH, "items": {34: 100}}) is False


def test_wrong_quantity_fails():
    assert contract_matches(**{**_MATCH, "items": {34: 99, 35: 5}}) is False
