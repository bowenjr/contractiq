from datetime import UTC, datetime

from core.approval_rules import approval_gaps

AS_OF = datetime(2026, 6, 1, tzinfo=UTC)


def _effective_policy() -> dict:
    return {
        "lifecycle_state": "PUBLISHED",
        "effective_from": "2026-01-01T00:00:00+00:00",
        "effective_until": None,
    }


def _active_case(case_id: str = "CASE-1", bid_id: str = "B-2026-0001") -> dict:
    return {"lifecycle_state": "ACTIVE", "bid_id": bid_id, "case_id": case_id}


def _route(case_id: str, state: str) -> dict:
    return {"case_id": case_id, "state": state}


def _codes(gaps: list[dict[str, str]]) -> set[str]:
    return {gap["code"] for gap in gaps}


def test_no_matching_route_is_not_determined() -> None:
    gaps = approval_gaps(
        cases=[_active_case()],
        routes=[],
        policies=[_effective_policy()],
        as_of=AS_OF,
    )
    codes = _codes(gaps)
    assert "APPROVAL_ROUTE_NOT_DETERMINED" in codes
    assert "APPROVAL_ROUTE_AMBIGUOUS" not in codes


def test_single_route_is_not_ambiguous() -> None:
    gaps = approval_gaps(
        cases=[_active_case()],
        routes=[_route("CASE-1", state="PENDING")],
        policies=[_effective_policy()],
        as_of=AS_OF,
    )
    codes = _codes(gaps)
    # The single route is evaluated (its PENDING state surfaces), but there is
    # no ambiguity because exactly one route matches the case.
    assert "APPROVAL_PENDING" in codes
    assert "APPROVAL_ROUTE_AMBIGUOUS" not in codes
    assert "APPROVAL_ROUTE_NOT_DETERMINED" not in codes


def test_multiple_routes_are_ambiguous() -> None:
    gaps = approval_gaps(
        cases=[_active_case()],
        routes=[
            _route("CASE-1", state="PENDING"),
            _route("CASE-1", state="APPROVED"),
        ],
        policies=[_effective_policy()],
        as_of=AS_OF,
    )
    codes = _codes(gaps)
    # Ambiguity is surfaced, and the function short-circuits: it must not report
    # a false-confidence gap (e.g. APPROVAL_PENDING) from an arbitrary route.
    assert "APPROVAL_ROUTE_AMBIGUOUS" in codes
    assert "APPROVAL_PENDING" not in codes
    ambiguous = next(g for g in gaps if g["code"] == "APPROVAL_ROUTE_AMBIGUOUS")
    assert ambiguous["severity"] == "BLOCKING_ATTENTION"
    assert ambiguous["dedup_key"] == "CASE-1:APPROVAL_ROUTE_AMBIGUOUS"
