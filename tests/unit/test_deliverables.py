from datetime import date

from core.deliverable_rules import calculate_deliverable_gaps, deliverable_metrics


def test_required_deliverable_gap_projection_is_deterministic() -> None:
    rows = [
        {
            "deliverable_id": "DEL-1",
            "bid_id": "B-1",
            "criticality": "MANDATORY",
            "condition_active": 1,
            "workflow_state": "ACTIVE",
            "direction": "INTERNAL",
            "due_basis": "UNSCHEDULED",
            "owner": None,
            "recipient": None,
        }
    ]
    gaps = calculate_deliverable_gaps(rows, as_of=date(2026, 1, 1))
    assert [gap.code for gap in gaps] == sorted(gap.code for gap in gaps)
    assert "DELIVERABLE_REQUIRED_NO_OWNER" in {gap.code for gap in gaps}
    assert deliverable_metrics(gaps, rows)["blocking_gaps"] == len(gaps)


def test_satisfied_and_cancelled_rows_are_excluded() -> None:
    rows = [
        {
            "deliverable_id": "DEL-1",
            "bid_id": "B-1",
            "workflow_state": "SATISFIED",
            "criticality": "MANDATORY",
        },
        {
            "deliverable_id": "DEL-2",
            "bid_id": "B-1",
            "workflow_state": "CANCELLED",
            "criticality": "MANDATORY",
        },
    ]
    assert calculate_deliverable_gaps(rows, as_of=date(2026, 1, 1)) == []
