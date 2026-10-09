"""OPS-13A U2: the six-task guided workflow is a pure projection."""

from __future__ import annotations

from datetime import date

from core.bid_workflow import OutstandingItem, PackageIntakeFacts, StageStatus, project_bid_workflow
from core.guided_workflow import TaskState
from core.ops07w import build_navigator
from core.owner_decision_evidence import build_owner_decision_evidence_snapshot
from core.owner_decisions import OwnerDecisionEvidenceSnapshot, OwnerDecisionValue

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


def _evidence(
    *,
    total: int = 2,
    reviewed: int = 2,
    state: str | None = "SUCCEEDED",
    requirements: int = 1,
    accepted: int = 0,
) -> OwnerDecisionEvidenceSnapshot:
    job = None
    if state is not None:
        job = {
            "job_id": "JOB-1",
            "state": state,
            "files_total": 2,
            "files_done": 2,
            "files_failed": 1 if state == "PARTIAL" else 0,
            "passages_total": 8,
            "passages_analysed": 7,
            "passages_failed": 1 if state == "PARTIAL" else 0,
        }
    return build_owner_decision_evidence_snapshot(
        received_files_total=total,
        received_files_reviewed=reviewed,
        latest_discovery_job=job,
        discovery_partial_or_failed_files=1 if state == "PARTIAL" else 0,
        requirement_count=requirements,
        accepted_deliverable_count=accepted,
    )


def _intake(**changes: object) -> PackageIntakeFacts:
    facts: dict[str, object] = {
        "releases_received": 1,
        "releases_incorporated": 0,
        "files_requiring_review": 0,
        "expected_missing": 0,
        "basis_published": False,
        "latest_release_id": "REL-1",
        "included_files": 2,
        "discovery_known": True,
        "discovery_latest_job_id": "JOB-1",
        "discovery_latest_state": "SUCCEEDED",
    }
    facts.update(changes)
    return PackageIntakeFacts(**facts)


def _item(
    *, scope: str, heading: str = "Resolve scope gap", blocking: bool = True
) -> OutstandingItem:
    return OutstandingItem(
        heading=heading,
        detail="A required control is open.",
        consequence="The Bid cannot pass the applicable control.",
        owner="Jason",
        owing_party="Bid team",
        due_date=date(2026, 10, 10),
        evidence_source="Test register",
        resolve_label=f"Resolve {heading.lower()}",
        resolve_path=f"/bids/{BID}/requirements-scope",
        blocking=blocking,
        scope=scope,
    )


def _project(
    *,
    intake: PackageIntakeFacts | None = None,
    evidence: OwnerDecisionEvidenceSnapshot | None = None,
    decision: OwnerDecisionValue | None = None,
    status: str = "active",
    verdict: str = "hold",
    outstanding: tuple[OutstandingItem, ...] = (),
):
    return project_bid_workflow(
        BID,
        bid_status=status,
        navigator=build_navigator(BID, COUNTS, verdict, "Jason"),
        intake=intake
        or PackageIntakeFacts(
            releases_received=0,
            releases_incorporated=0,
            files_requiring_review=0,
            expected_missing=0,
        ),
        readiness_verdict=verdict,
        outstanding=outstanding,
        owner_decision=decision,
        owner_decision_reason="The owner stopped pursuit." if decision else None,
        owner_decision_evidence=evidence,
    )


def test_jasons_partial_state_passes_analysis_with_open_items() -> None:
    projection = _project(
        intake=_intake(
            files_requiring_review=10,
            discovery_latest_state="PARTIAL",
            discovery_files_incomplete=1,
        ),
        evidence=_evidence(
            total=12,
            reviewed=2,
            state="PARTIAL",
            requirements=0,
            accepted=3,
        ),
    )
    assert [task.state for task in projection.tasks[:4]] == [
        TaskState.DONE,
        TaskState.DONE_OPEN,
        TaskState.DONE,
        TaskState.CURRENT,
    ]
    assert projection.current_task_key == "decide"
    assert projection.stages[1].status is StageStatus.COMPLETE_OPEN
    assert projection.stages[1].status.slug == "complete-open"
    assert (projection.next_action_label, projection.next_action_path) == (
        "Record owner decision",
        f"/bids/{BID}/owner-decision",
    )
    assert projection.secondary_action_label == "Review 10 undecided file(s)"


def test_continue_skips_done_and_done_open_tasks_to_current_decision() -> None:
    projection = _project(
        intake=_intake(
            files_requiring_review=10,
            discovery_latest_state="PARTIAL",
            discovery_files_incomplete=1,
        ),
        evidence=_evidence(
            total=12,
            reviewed=2,
            state="PARTIAL",
            requirements=0,
            accepted=3,
        ),
    )
    assert [task.state for task in projection.tasks[:4]] == [
        TaskState.DONE,
        TaskState.DONE_OPEN,
        TaskState.DONE,
        TaskState.CURRENT,
    ]
    decision = projection.task("decide")
    assert decision is not None
    for key in ("receive", "analyse", "review"):
        task = projection.task(key)
        assert task is not None
        assert task.continue_label == f"Continue to {decision.label}"
        assert task.continue_path == decision.primary_path


def test_continue_from_done_decision_targets_current_prepare_task() -> None:
    projection = _project(
        intake=_intake(),
        evidence=_evidence(),
        decision=OwnerDecisionValue.PURSUE,
    )
    decision = projection.task("decide")
    prepare = projection.task("prepare")
    assert decision is not None
    assert prepare is not None
    assert prepare.state is TaskState.CURRENT
    assert decision.continue_label == f"Continue to {prepare.label}"
    assert decision.continue_path == prepare.primary_path


def test_continue_from_review_skips_done_decision_and_prepare_to_current_issue() -> None:
    projection = _project(
        intake=_intake(),
        evidence=_evidence(),
        decision=OwnerDecisionValue.PURSUE,
        verdict="clear",
    )
    review = projection.task("review")
    decision = projection.task("decide")
    prepare = projection.task("prepare")
    issue = projection.task("issue")
    assert review is not None
    assert decision is not None
    assert prepare is not None
    assert issue is not None
    assert [review.state, decision.state, prepare.state, issue.state] == [
        TaskState.DONE,
        TaskState.DONE,
        TaskState.DONE,
        TaskState.CURRENT,
    ]
    assert review.continue_label == f"Continue to {issue.label}"
    assert review.continue_path == issue.primary_path


def test_stopped_bid_never_continues_to_stopped_prepare_or_issue_tasks() -> None:
    projection = _project(
        intake=_intake(),
        evidence=_evidence(),
        decision=OwnerDecisionValue.DO_NOT_PURSUE,
        status="no_bid",
    )
    decision = projection.task("decide")
    prepare = projection.task("prepare")
    issue = projection.task("issue")
    assert decision is not None
    assert prepare is not None
    assert issue is not None
    assert decision.state is TaskState.CURRENT
    assert prepare.state is TaskState.STOPPED
    assert issue.state is TaskState.STOPPED
    assert [task.continue_path for task in projection.tasks[:3]] == [
        decision.primary_path,
        decision.primary_path,
        decision.primary_path,
    ]
    assert all(task.continue_path is None for task in projection.tasks[3:])
    assert all(
        task.continue_path not in {prepare.primary_path, issue.primary_path}
        for task in projection.tasks
    )


def test_issued_and_terminal_bids_have_no_continue_actions() -> None:
    for status in ("submitted", "won", "lost"):
        projection = _project(status=status)
        assert all(task.state is TaskState.DONE for task in projection.tasks)
        assert all(task.continue_label is None for task in projection.tasks)
        assert all(task.continue_path is None for task in projection.tasks)


def test_continue_targets_are_always_unfinished_and_applicable() -> None:
    projections = (
        _project(
            intake=_intake(
                files_requiring_review=10,
                discovery_latest_state="PARTIAL",
                discovery_files_incomplete=1,
            ),
            evidence=_evidence(
                total=12,
                reviewed=2,
                state="PARTIAL",
                requirements=0,
                accepted=3,
            ),
        ),
        _project(
            intake=_intake(),
            evidence=_evidence(),
            decision=OwnerDecisionValue.PURSUE,
        ),
        _project(
            intake=_intake(),
            evidence=_evidence(),
            decision=OwnerDecisionValue.PURSUE,
            verdict="clear",
        ),
        _project(
            intake=_intake(),
            evidence=_evidence(),
            decision=OwnerDecisionValue.DO_NOT_PURSUE,
            status="no_bid",
        ),
        _project(status="submitted"),
    )
    finished = {TaskState.DONE, TaskState.DONE_OPEN, TaskState.STOPPED}
    for projection in projections:
        for task in projection.tasks:
            if task.continue_label is None:
                assert task.continue_path is None
                continue
            target_label = task.continue_label.removeprefix("Continue to ")
            target = next(
                candidate for candidate in projection.tasks if candidate.label == target_label
            )
            assert target.state not in finished
            assert task.continue_path == target.primary_path


def test_completed_tasks_retain_primary_review_destinations() -> None:
    projection = _project(status="submitted")
    for task in projection.tasks:
        assert task.state is TaskState.DONE
        assert task.primary_label.strip()
        assert task.primary_path.strip()


def test_fresh_bid_starts_with_receipt() -> None:
    projection = _project()
    assert projection.current_task.key == "receive"
    assert projection.current_task.primary_label == "Import customer bid package"
    assert all(task.state is TaskState.UPCOMING for task in projection.tasks[1:])


def test_received_package_advances_to_analysis_while_expected_release_stays_visible() -> None:
    projection = _project(
        intake=_intake(
            included_files=0,
            files_requiring_review=2,
            discovery_latest_state=None,
            expected_missing=1,
        ),
        evidence=None,
    )
    receive = projection.task("receive")
    assert receive is not None
    assert receive.state is TaskState.DONE_OPEN
    assert projection.current_task.key == "analyse"
    assert receive.needs[0].text == "Receive the customer package."
    assert receive.later[0].text == ("Receive the expected customer release (1 not yet received).")
    assert receive.later[0].action_path.endswith("/import?mode=addendum")


def test_zero_included_active_and_failed_analysis_states() -> None:
    zero = _project(intake=_intake(included_files=0, discovery_latest_state=None), evidence=None)
    assert zero.current_task.key == "analyse"
    assert zero.current_task.primary_label == "Choose files for analysis"
    assert zero.current_task.alternative_label == "Add requirements manually instead"

    manual = _project(
        intake=_intake(included_files=0, discovery_latest_state=None),
        evidence=_evidence(state=None, requirements=1),
    )
    manual_analysis = manual.task("analyse")
    assert manual_analysis is not None
    assert manual_analysis.state is TaskState.DONE

    active = _project(intake=_intake(discovery_active_job_id="JOB-2"), evidence=_evidence())
    assert active.current_task.primary_label == "View discovery progress"

    failed = _project(
        intake=_intake(discovery_latest_state="FAILED", discovery_files_incomplete=1),
        evidence=_evidence(state="FAILED", requirements=0),
    )
    assert failed.current_task.primary_label == "Retry deliverables discovery"
    assert failed.current_task.alternative_path == f"/requirements?bid_id={BID}#create"


def test_pending_proposals_keep_review_current_but_always_offer_owner_decision() -> None:
    projection = _project(
        intake=_intake(discovery_proposals_pending=3),
        evidence=_evidence(),
    )
    review = projection.task("review")
    assert review is not None
    assert review.state is TaskState.CURRENT
    assert review.primary_label == "Review 3 proposed deliverable(s)"
    assert review.continue_path == f"/bids/{BID}/owner-decision"


def test_recorded_pursue_decision_makes_pending_review_items_non_blocking() -> None:
    projection = _project(
        intake=_intake(discovery_proposals_pending=2),
        evidence=_evidence(),
        decision=OwnerDecisionValue.PURSUE,
    )
    review = projection.task("review")
    assert review is not None
    assert review.state is TaskState.DONE_OPEN
    assert review.later[0].action_path.endswith("#proposals")


def test_stopped_and_legacy_bids_have_explicit_terminal_states() -> None:
    stopped = _project(
        intake=_intake(),
        evidence=_evidence(),
        decision=OwnerDecisionValue.DO_NOT_PURSUE,
        status="no_bid",
    )
    assert stopped.current_task.key == "decide"
    assert stopped.current_task.state is TaskState.CURRENT
    assert stopped.current_task.primary_label == "Record a new decision"
    assert [task.state for task in stopped.tasks[4:]] == [
        TaskState.STOPPED,
        TaskState.STOPPED,
    ]

    legacy = _project(status="submitted")
    assert all(task.state is TaskState.DONE for task in legacy.tasks)
    assert legacy.current_task.key == "issue"


def test_prepare_ignores_issue_phase_items_but_not_whole_bid_blockers() -> None:
    whole = _item(scope="whole_bid")
    blocked = _project(
        intake=_intake(),
        evidence=_evidence(),
        decision=OwnerDecisionValue.PURSUE,
        verdict="clear",
        outstanding=(whole,),
    )
    assert blocked.current_task.key == "prepare"
    assert blocked.current_task.primary_path == whole.resolve_path

    approval = _item(
        scope="pending_before_issue", heading="Senior approval missing", blocking=False
    )
    passed = _project(
        intake=_intake(),
        evidence=_evidence(),
        decision=OwnerDecisionValue.PURSUE,
        verdict="clear",
        outstanding=(approval,),
    )
    prepare = passed.task("prepare")
    assert prepare is not None
    assert prepare.state is TaskState.DONE_OPEN
    assert passed.current_task.key == "issue"


def test_issue_task_lists_undecided_basis_approval_and_issue_needs() -> None:
    approval = _item(
        scope="pending_before_issue", heading="Senior approval missing", blocking=False
    )
    projection = _project(
        intake=_intake(files_requiring_review=4),
        evidence=_evidence(total=6, reviewed=2),
        decision=OwnerDecisionValue.PURSUE,
        verdict="clear",
        outstanding=(approval,),
    )
    issue = projection.task("issue")
    assert issue is not None
    assert [need.met for need in issue.needs] == [True, False, False, False, False]
    assert [need.text for need in issue.needs] == [
        "Receive every release known to be missing.",
        "Decide how every received file will be used.",
        "Publish a Bid Basis containing every received release.",
        "Obtain every approval required before issue.",
        "Issue the proposal to the customer.",
    ]
    assert issue.primary_label == "Review received package files"


def test_issue_task_requires_every_known_release_to_be_received() -> None:
    projection = _project(
        intake=_intake(expected_missing=1),
        evidence=_evidence(),
        decision=OwnerDecisionValue.PURSUE,
        verdict="clear",
    )
    issue = projection.task("issue")
    assert issue is not None
    assert issue.state is not TaskState.DONE
    assert issue.needs[0].text == "Receive every release known to be missing."
    assert issue.needs[0].met is False
    assert issue.needs[0].action_label == "Import the expected customer release"


def test_task_for_page_prefers_current_then_passed_then_first_candidate() -> None:
    decision = _project(intake=_intake(), evidence=_evidence())
    assert (
        decision.task_for_page(f"/bids/{BID}/deliverable-discovery?tab=proposals").key == "analyse"
    )  # type: ignore[union-attr]

    issue = _project(
        intake=_intake(),
        evidence=_evidence(),
        decision=OwnerDecisionValue.PURSUE,
        verdict="clear",
    )
    assert issue.task_for_page(f"/bids/{BID}/package-intake-addenda").key == "issue"  # type: ignore[union-attr]
    assert issue.task_for_page("/unknown") is None


def test_next_action_fields_are_the_current_tasks_primary_action() -> None:
    for projection in (
        _project(),
        _project(intake=_intake(), evidence=_evidence()),
        _project(
            intake=_intake(),
            evidence=_evidence(),
            decision=OwnerDecisionValue.PURSUE,
            verdict="clear",
        ),
    ):
        assert projection.next_action_label == projection.current_task.primary_label
        assert projection.next_action_path == projection.current_task.primary_path
        assert projection.next_action_stage_label == projection.current_task.label
