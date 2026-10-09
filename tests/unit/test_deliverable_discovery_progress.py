"""U3 current-source progress and workflow regression coverage."""

from __future__ import annotations

from core.bid_workflow import PackageIntakeFacts, project_bid_workflow
from core.deliverable_discovery_progress import discovery_source_progress
from core.guided_workflow import TaskState
from core.ops07w import build_navigator
from core.owner_decision_evidence import build_owner_decision_evidence_snapshot

BID = "B-2026-0001"
COUNTS = {
    "documents": 0,
    "requirements": 0,
    "responses": 0,
    "scopes": 0,
    "interfaces": 0,
    "packages": 0,
    "manufacturer_associations": 0,
    "manufacturer_requirements": 0,
    "manufacturer_complete": 0,
    "manufacturer_blocked": 0,
    "commercial": 0,
    "risks": 0,
    "decisions": 0,
    "approvals": 0,
    "proposals": 0,
    "deliverables": 0,
    "assessments": 1,
}


def _outcome(
    file_id: str,
    outcome: str,
    *,
    total: int,
    analysed: int,
    completed_at: str,
) -> dict[str, object]:
    return {
        "received_file_id": file_id,
        "outcome": outcome,
        "passages_total": total,
        "passages_analysed": analysed,
        "run_completed_at": completed_at,
    }


def test_earlier_full_result_survives_later_unreadable_retry() -> None:
    progress = discovery_source_progress(
        {"FILE-1"},
        [
            _outcome(
                "FILE-1",
                "ANALYSED_CANDIDATES",
                total=9,
                analysed=9,
                completed_at="2026-10-01T10:00:00+00:00",
            ),
            _outcome(
                "FILE-1",
                "UNREADABLE",
                total=0,
                analysed=0,
                completed_at="2026-10-02T10:00:00+00:00",
            ),
        ],
    )

    assert progress.effective_state == "SUCCEEDED"
    assert progress.fully_analysed == 1
    assert progress.not_readable == 0
    assert progress.retry_worthwhile is False


def test_partly_analysed_result_survives_a_worse_retry() -> None:
    progress = discovery_source_progress(
        {"FILE-1"},
        [
            _outcome(
                "FILE-1",
                "PARTIALLY_ANALYSED",
                total=8,
                analysed=5,
                completed_at="2026-10-01T10:00:00+00:00",
            ),
            _outcome(
                "FILE-1",
                "MODEL_UNAVAILABLE",
                total=8,
                analysed=0,
                completed_at="2026-10-02T10:00:00+00:00",
            ),
        ],
    )

    assert progress.effective_state == "PARTIAL"
    assert progress.partly_analysed == 1
    assert progress.recoverable == 0
    assert progress.passages_unanalysed == 3


def test_unreadable_only_package_is_failed_without_a_worthwhile_retry() -> None:
    progress = discovery_source_progress(
        {"FILE-1"},
        [
            _outcome(
                "FILE-1",
                "EXTRACTED_NO_TEXT",
                total=0,
                analysed=0,
                completed_at="2026-10-01T10:00:00+00:00",
            )
        ],
    )

    assert progress.effective_state == "FAILED"
    assert progress.not_readable == 1
    assert progress.retry_worthwhile is False


def test_newly_included_file_without_a_result_keeps_work_open() -> None:
    progress = discovery_source_progress(
        {"FILE-1", "FILE-2"},
        [
            _outcome(
                "FILE-1",
                "ANALYSED_ZERO_CANDIDATES",
                total=4,
                analysed=4,
                completed_at="2026-10-01T10:00:00+00:00",
            )
        ],
    )

    assert progress.effective_state == "PARTIAL"
    assert progress.fully_analysed == 1
    assert progress.never_attempted == 1
    assert progress.retry_worthwhile is True


def test_outcome_for_a_now_excluded_file_is_ignored() -> None:
    progress = discovery_source_progress(
        {"FILE-1"},
        [
            _outcome(
                "FILE-1",
                "ANALYSED_CANDIDATES",
                total=2,
                analysed=2,
                completed_at="2026-10-01T10:00:00+00:00",
            ),
            _outcome(
                "FILE-OLD",
                "MODEL_UNAVAILABLE",
                total=7,
                analysed=0,
                completed_at="2026-10-02T10:00:00+00:00",
            ),
        ],
    )

    assert progress.effective_state == "SUCCEEDED"
    assert progress.recoverable == 0
    assert progress.passages_unanalysed == 0


def test_no_applicable_outcomes_has_no_effective_state() -> None:
    progress = discovery_source_progress({"FILE-1"}, [])

    assert progress.effective_state is None
    assert progress.has_results is False
    assert progress.never_attempted == 1
    assert progress.retry_worthwhile is True


def _workflow(intake: PackageIntakeFacts):
    evidence = build_owner_decision_evidence_snapshot(
        received_files_total=10,
        received_files_reviewed=10,
        latest_discovery_job={
            "job_id": "JOB-RETRY",
            "state": "FAILED",
            "files_total": 1,
            "files_done": 0,
            "files_failed": 1,
            "passages_total": 0,
            "passages_analysed": 0,
            "passages_failed": 0,
        },
        discovery_partial_or_failed_files=1,
        requirement_count=0,
        accepted_deliverable_count=3,
    )
    return project_bid_workflow(
        BID,
        bid_status="active",
        navigator=build_navigator(BID, COUNTS, "hold", "Jason"),
        intake=intake,
        readiness_verdict="hold",
        outstanding=(),
        owner_decision_evidence=evidence,
    )


def _intake(**changes: object) -> PackageIntakeFacts:
    values: dict[str, object] = {
        "releases_received": 1,
        "releases_incorporated": 0,
        "files_requiring_review": 0,
        "expected_missing": 0,
        "latest_release_id": "REL-1",
        "included_files": 10,
        "discovery_latest_job_id": "JOB-RETRY",
        "discovery_latest_state": "PARTIAL",
    }
    values.update(changes)
    return PackageIntakeFacts(**values)


def test_unreadable_retry_regression_stays_past_analysis_without_retry_action() -> None:
    projection = _workflow(_intake(discovery_files_not_readable=1))
    analysis = projection.task("analyse")

    assert analysis is not None
    assert analysis.state is TaskState.DONE_OPEN
    assert projection.current_task_key == "decide"
    action_labels = [analysis.primary_label]
    action_labels.extend(need.action_label or "" for need in analysis.needs)
    action_labels.extend(item.action_label for item in analysis.later)
    assert not any("Retry" in label for label in action_labels)
    assert [(item.text, item.action_label) for item in analysis.later] == [
        (
            "1 Included file(s) could not be read by analysis "
            "(unsupported type or no readable text).",
            "See which files",
        )
    ]


def test_genuinely_recoverable_failure_still_offers_retry() -> None:
    projection = _workflow(
        _intake(
            discovery_latest_state="FAILED",
            discovery_files_incomplete=1,
        )
    )
    analysis = projection.task("analyse")

    assert analysis is not None
    assert analysis.primary_label == "Retry deliverables discovery"
