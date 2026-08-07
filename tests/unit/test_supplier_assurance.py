"""Focused TASK-11 supplier assurance tests using isolated synthetic state."""

from datetime import date

from core.supplier_assurance_rules import calculate_gaps


def test_silent_and_exclusion_gap_codes_are_deterministic() -> None:
    requests = [{"request_id": "REQ-1", "bid_id": "B-1", "request_state": "ISSUED"}]
    items = [
        {"request_item_id": "ITEM-1", "request_id": "REQ-1", "scope_offer_position": "INCLUDED"}
    ]
    responses = [
        {
            "response_id": "RESP-1",
            "response_version_id": "VER-1",
            "request_id": "REQ-1",
            "bid_id": "B-1",
            "version_number": 1,
            "review_state": "ACCEPTED",
            "validity_state": "DATE_PROVIDED",
            "valid_until": "2026-08-20",
        }
    ]
    coverage = [{"request_item_id": "ITEM-1", "state": "EXCEPTION", "exception_kind": "EXCLUSION"}]
    gaps = calculate_gaps(requests, items, responses, coverage, as_of_date=date(2026, 8, 7))
    assert [gap.code for gap in gaps] == [
        "SUPPLIER_EXCLUDED_CUSTOMER_INCLUDED",
        "SUPPLIER_RESPONSE_EXCEPTION",
    ]


def test_empty_population_has_no_false_positive() -> None:
    assert calculate_gaps([], [], [], [], as_of_date=date(2026, 8, 7)) == ()
