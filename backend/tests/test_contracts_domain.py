"""Pure contract-matching rules (ADR-0037): lifecycle-status derivation and the
items/price/location match check. No I/O — see test_corp_contracts.py for the use case."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.contracts import (
    AppraisalFacts,
    ContractObservation,
    contract_matches,
    derive_lifecycle_status,
    match_appraisal_id,
    resolve_best_links,
)

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


# --- match_appraisal_id ---

_AID = uuid.uuid4()
_ID_MAP = {"abcdefghijkl": _AID}  # a 12-char base64url public_id


def test_matches_an_exact_title_run():
    assert match_appraisal_id("buyback abcdefghijkl please", _ID_MAP) == _AID


def test_matches_a_window_inside_a_longer_run():
    # Underscores/hyphens are part of a run, so the id is buried — found by window scan.
    assert match_appraisal_id("note_abcdefghijkl_note", _ID_MAP) == _AID


def test_is_case_sensitive_and_exact():
    assert match_appraisal_id("ABCDEFGHIJKL", _ID_MAP) is None
    assert match_appraisal_id("abcdefghijk", _ID_MAP) is None  # 11 chars, too short


def test_no_title_or_no_match_is_none():
    assert match_appraisal_id(None, _ID_MAP) is None
    assert match_appraisal_id("", _ID_MAP) is None
    assert match_appraisal_id("some other contract", _ID_MAP) is None


# --- resolve_best_links ---


def _obs(appraisal_id, contract_id, lifecycle, *, items=None, issued=NOW,
         price=Decimal("100"), location=1, completed=None):
    return ContractObservation(
        appraisal_id=appraisal_id, contract_id=contract_id, lifecycle=lifecycle,
        issued_at=issued, completed_at=completed,
        price=price, start_location_id=location, items=items or {},
    )


# Facts a matching validatable contract must satisfy: 100 ISK at location "1", item 34×10.
_FACTS = {_AID: AppraisalFacts(Decimal("100"), "1", {34: 10})}


def test_validatable_match_keeps_its_lifecycle():
    obs = [_obs(_AID, 1, "in_progress", items={34: 10})]
    links = resolve_best_links(obs, _FACTS)
    assert links[_AID].status == "in_progress"
    assert links[_AID].contract_id == 1


def test_validatable_nonmatch_is_mismatch():
    obs = [_obs(_AID, 1, "completed", items={34: 9})]  # short quantity
    assert resolve_best_links(obs, _FACTS)[_AID].status == "mismatch"


def test_missing_facts_for_validatable_is_mismatch():
    obs = [_obs(_AID, 1, "in_progress", items={34: 10})]
    assert resolve_best_links(obs, {})[_AID].status == "mismatch"


def test_voided_is_surfaced_without_facts():
    # A void contract needs no facts/items — taken at face value.
    obs = [_obs(_AID, 1, "rejected")]
    assert resolve_best_links(obs, {})[_AID].status == "rejected"


def test_prefers_active_over_voided_for_one_appraisal():
    obs = [
        _obs(_AID, 1, "rejected", issued=PAST),
        _obs(_AID, 2, "in_progress", items={34: 10}, issued=NOW),
    ]
    best = resolve_best_links(obs, _FACTS)[_AID]
    assert (best.status, best.contract_id) == ("in_progress", 2)


def test_ties_break_on_newest_issued():
    older, newer = NOW - timedelta(hours=1), NOW
    obs = [
        _obs(_AID, 1, "rejected", issued=older),
        _obs(_AID, 2, "cancelled", issued=newer),  # same priority bucket (void)
    ]
    assert resolve_best_links(obs, {})[_AID].contract_id == 2
