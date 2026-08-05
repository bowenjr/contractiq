from datetime import UTC, date, datetime
from uuid import UUID

from core.enums import Actor
from core.my_day import RequirementAttentionSnapshot, project_my_day
from core.requirement_coverage import calculate_requirement_coverage
from core.requirements import Requirement
from core.schemas import Provenance

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)


def _requirement(number: int, **changes: object) -> Requirement:
    values: dict[str, object] = {
        "requirement_id": f"REQ-{UUID(int=number)}",
        "bid_id": "B-1",
        "title": f"Requirement {number}",
        "statement": "Synthetic requirement statement",
        "origin": "INTERNAL",
        "category": "OTHER",
        "significance": "INFORMATIONAL",
        "lifecycle_stage": "BID",
        "lifecycle_state": "ACTIVE",
        "disposition": "UNASSESSED",
        "work_state": "OPEN",
        "review_state": "NOT_REVIEWED",
        "created_at": NOW,
        "updated_at": NOW,
        "version": 1,
        "provenance": Provenance(
            created_by=Actor.HUMAN,
            created_at=NOW,
            human_confirmed=True,
            confirmed_by="author",
            confirmed_at=NOW,
        ),
    }
    values.update(changes)
    return Requirement.model_validate(values)


def test_empty_coverage_is_no_data_not_false_complete() -> None:
    result = calculate_requirement_coverage([], date(2026, 8, 5))
    assert result.total_active == 0
    assert result.assessed.percentage is None
    assert result.fully_closed.percentage is None


def test_fixed_date_counts_percentages_exceptions_and_active_denominator() -> None:
    records = [
        _requirement(
            1,
            title="Overdue mandatory",
            significance="MANDATORY",
            due_date=date(2026, 8, 4),
            source_document_id="DOC-1",
            source_document_version_id="DV-1",
        ),
        _requirement(
            2,
            disposition="DEVIATE",
            response_text="Synthetic deviation",
            work_state="COMPLETE",
            review_state="ACCEPTED",
            reviewer="reviewer",
            owner="owner",
            due_date=date(2026, 8, 5),
        ),
        _requirement(
            3,
            disposition="COMPLY",
            response_text="Synthetic response",
            work_state="COMPLETE",
            review_state="ACCEPTED",
            reviewer="reviewer",
            owner="owner",
        ),
        _requirement(4, lifecycle_state="WITHDRAWN"),
    ]
    first = calculate_requirement_coverage(records, date(2026, 8, 5))
    second = calculate_requirement_coverage(records, date(2026, 8, 5))
    assert first == second
    assert first.total_active == 3
    assert first.assessed.model_dump() == {
        "numerator": 2,
        "denominator": 3,
        "percentage": 66.7,
    }
    assert first.fully_closed.numerator == 2
    assert first.exception_counts["DEVIATE"] == 1
    assert first.exception_total == 1
    assert first.high_attention == 1
    assert first.overdue == 1
    assert first.due_today == 0
    assert first.ownerless == 1
    assert first.source_coverage.numerator == 1


def test_my_day_requirement_attention_is_deduplicated_distinct_and_stable() -> None:
    overdue = _requirement(
        1,
        title="B requirement",
        significance="MANDATORY",
        due_date=date(2026, 8, 4),
    )
    today = _requirement(2, title="A requirement", due_date=date(2026, 8, 5))
    projection = project_my_day(
        [],
        [],
        date(2026, 8, 5),
        7,
        [
            RequirementAttentionSnapshot(requirement=today, bid_name="Synthetic"),
            RequirementAttentionSnapshot(requirement=overdue, bid_name="Synthetic"),
        ],
    )
    assert [item.requirement.requirement_id for item in projection.requirement_attention] == [
        overdue.requirement_id,
        today.requirement_id,
    ]
    assert projection.requirement_attention[0].reasons == ["OVERDUE", "HIGH_ATTENTION"]
    assert projection.counts.requirement_attention == 2
    assert projection.counts.requirement_overdue == 1
    assert projection.counts.requirement_due_today == 1
    assert projection.overdue == []
