"""OPS-13A UNIT A1: the intake discovery and secondary actions are pure derivations."""

from __future__ import annotations

from core.bid_workflow import (
    PackageIntakeFacts,
    StageStatus,
    _package_intake_stage,
    intake_discovery_action,
    intake_secondary_action,
    project_bid_workflow,
)
from core.ops07w import NavigatorStage

BID = "B-2026-0001"
BASE = f"/bids/{BID}/deliverable-discovery"


def _facts(**changes: object) -> PackageIntakeFacts:
    base: dict[str, object] = {
        "releases_received": 1,
        "releases_incorporated": 0,
        "files_requiring_review": 0,
        "expected_missing": 0,
        "latest_release_id": "REL-1",
        "included_files": 3,
    }
    base.update(changes)
    return PackageIntakeFacts(**base)


def test_active_job_wins_over_every_other_discovery_state() -> None:
    facts = _facts(
        discovery_active_job_id="JOB-9",
        discovery_latest_state="FAILED",
        discovery_latest_job_id="JOB-8",
    )
    assert intake_discovery_action(BID, facts) == (
        "View discovery progress",
        f"{BASE}/jobs/JOB-9",
    )


def test_zero_included_files_never_advertises_discovery() -> None:
    facts = _facts(included_files=0, discovery_latest_state="FAILED")
    assert intake_discovery_action(BID, facts) is None


def test_included_files_never_analysed_offer_a_first_run() -> None:
    facts = _facts(included_files=3)
    assert intake_discovery_action(BID, facts) == ("Run deliverables discovery", BASE)


def test_partial_latest_job_offers_a_review_of_its_results() -> None:
    facts = _facts(discovery_latest_state="PARTIAL", discovery_latest_job_id="JOB-4")
    assert intake_discovery_action(BID, facts) == (
        "Review partial discovery results",
        f"{BASE}/jobs/JOB-4",
    )


def test_failed_latest_job_offers_a_retry() -> None:
    facts = _facts(
        discovery_latest_state="FAILED",
        discovery_latest_job_id="JOB-5",
        discovery_files_incomplete=1,
    )
    assert intake_discovery_action(BID, facts) == (
        "Retry deliverables discovery",
        f"{BASE}/jobs/JOB-5",
    )


def test_succeeded_job_with_pending_proposals_offers_a_review() -> None:
    facts = _facts(
        discovery_latest_state="SUCCEEDED",
        discovery_latest_job_id="JOB-6",
        discovery_proposals_pending=2,
    )
    assert intake_discovery_action(BID, facts) == (
        "Review proposed deliverables",
        f"{BASE}#proposals",
    )


def test_succeeded_job_with_nothing_pending_and_nothing_unanalysed_is_quiet() -> None:
    facts = _facts(
        discovery_latest_state="SUCCEEDED",
        discovery_latest_job_id="JOB-7",
        discovery_proposals_pending=0,
        discovery_included_unanalysed=0,
    )
    assert intake_discovery_action(BID, facts) is None


def test_succeeded_job_with_unanalysed_included_files_offers_a_rerun() -> None:
    facts = _facts(
        discovery_latest_state="SUCCEEDED",
        discovery_latest_job_id="JOB-7",
        discovery_proposals_pending=0,
        discovery_included_unanalysed=1,
    )
    assert intake_discovery_action(BID, facts) == ("Run deliverables discovery", BASE)


def test_secondary_action_is_present_only_while_files_await_review() -> None:
    pending = _facts(files_requiring_review=2)
    assert intake_secondary_action(BID, pending) == (
        "Finish reviewing files",
        f"/bids/{BID}/package-intake-addenda/releases/REL-1#files",
    )
    reviewed = _facts(files_requiring_review=0)
    assert intake_secondary_action(BID, reviewed) is None


def test_secondary_action_needs_a_release_to_anchor_the_review() -> None:
    facts = _facts(files_requiring_review=2, latest_release_id=None)
    assert intake_secondary_action(BID, facts) is None


def test_known_discovery_with_no_included_files_asks_for_file_selection() -> None:
    stage = _package_intake_stage(
        BID,
        _facts(
            files_requiring_review=10,
            included_files=0,
            discovery_known=True,
        ),
    )
    assert stage[2] == "Choose files for analysis"
    assert stage[3].endswith("#files")


def test_included_files_offer_discovery_while_undecided_files_remain() -> None:
    stage = _package_intake_stage(
        BID,
        _facts(
            files_requiring_review=10,
            included_files=2,
            discovery_known=True,
        ),
    )
    assert stage[2] == "Run deliverables discovery"
    assert "10 file(s) still undecided" in stage[1]


def test_active_discovery_job_is_the_package_intake_action() -> None:
    stage = _package_intake_stage(
        BID,
        _facts(discovery_known=True, discovery_active_job_id="JOB-9"),
    )
    assert stage[2] == "View discovery progress"


def test_completed_discovery_and_review_complete_package_intake() -> None:
    stage = _package_intake_stage(
        BID,
        _facts(
            files_requiring_review=0,
            included_files=2,
            discovery_known=True,
            discovery_latest_state="SUCCEEDED",
            discovery_proposals_pending=0,
            discovery_included_unanalysed=0,
        ),
    )
    assert stage[0] is StageStatus.COMPLETE


def test_default_discovery_facts_ask_for_file_selection() -> None:
    facts = PackageIntakeFacts(
        releases_received=1,
        releases_incorporated=0,
        files_requiring_review=3,
        expected_missing=0,
        latest_release_id="REL-1",
    )
    stage = _package_intake_stage(BID, facts)
    assert stage[2] == "Choose files for analysis"


def test_projection_offers_file_review_as_secondary_to_discovery() -> None:
    setup = NavigatorStage(
        key="setup",
        label="Bid setup",
        status="Complete",
        completeness="Setup complete",
        primary_label="Review setup",
        primary_path=f"/bids/{BID}/classification",
        detail_path=f"/bids/{BID}",
    )
    projection = project_bid_workflow(
        BID,
        bid_status="active",
        navigator=(setup,),
        intake=_facts(
            files_requiring_review=10,
            included_files=2,
            discovery_known=True,
        ),
        readiness_verdict="hold",
        outstanding=(),
    )
    assert projection.next_action_label == "Run deliverables discovery"
    assert projection.secondary_action_label == "Review 10 undecided file(s)"
    assert projection.secondary_action_path is not None
    assert projection.secondary_action_path.endswith("#files")
