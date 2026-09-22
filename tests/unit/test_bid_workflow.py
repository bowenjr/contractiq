"""OPS-11BX: the nine-stage Bid workflow spine is a truthful pure projection."""

from __future__ import annotations

import pathlib
import re
from datetime import date

import pytest

from core.bid_workflow import (
    BLOCKER_HEADINGS,
    INTAKE_HEADINGS,
    PROPOSAL_HEADINGS,
    OutstandingItem,
    PackageIntakeFacts,
    StageStatus,
    blocker_heading,
    intake_heading,
    project_bid_workflow,
    proposal_heading,
)
from core.gates import GateContext, evaluate_all_gates
from core.ops07w import NavigatorStage, build_navigator

BID = "B-2026-0001"

EMPTY_COUNTS: dict[str, int] = {
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

NO_PACKAGE = PackageIntakeFacts(
    releases_received=0,
    releases_incorporated=0,
    files_requiring_review=0,
    expected_missing=0,
)


def _navigator(**changes: int) -> list[NavigatorStage]:
    counts = dict(EMPTY_COUNTS)
    counts.update(changes)
    return build_navigator(BID, counts, "hold", "Jason")


def _project(
    intake: PackageIntakeFacts = NO_PACKAGE,
    *,
    verdict: str = "hold",
    bid_status: str = "active",
    outstanding: tuple[OutstandingItem, ...] = (),
    **counts: int,
):
    return project_bid_workflow(
        BID,
        bid_status=bid_status,
        navigator=_navigator(**counts),
        intake=intake,
        readiness_verdict=verdict,
        outstanding=outstanding,
    )


def _stage(projection, key: str):
    return next(stage for stage in projection.stages if stage.key == key)


def test_spine_is_exactly_the_nine_required_stages_in_order() -> None:
    projection = _project()
    assert [stage.label for stage in projection.stages] == [
        "Bid setup",
        "Package intake and addenda",
        "Controlled customer documents",
        "Requirements and responses",
        "Scope and interfaces",
        "Manufacturers and supplier coverage",
        "Commercial, contract risk and approvals",
        "Proposal and negotiation",
        "Bid Basis and handover",
    ]
    assert [stage.number for stage in projection.stages] == list(range(1, 10))


def test_new_bid_leads_with_package_ingestion_not_a_register() -> None:
    projection = _project()
    assert projection.next_action_label == "Import customer bid package"
    assert projection.next_action_path == (
        f"/bids/{BID}/package-intake-addenda/import?mode=initial"
    )
    assert projection.next_action_stage_label == "Package intake and addenda"
    # The old navigator recommended these before any package had been received.
    assert projection.next_action_label != "Register customer source"
    assert _stage(projection, "documents").actionable is False
    assert _stage(projection, "requirements").actionable is False
    assert _stage(projection, "scope").actionable is False


def test_exactly_one_next_action_is_derived_from_the_earliest_incomplete_stage() -> None:
    projection = _project()
    candidates = [
        stage
        for stage in projection.stages
        if not stage.settled and stage.actionable and stage.action_label
    ]
    assert candidates[0].action_label == projection.next_action_label
    assert projection.current_stage.key == "package-intake"


def test_package_intake_advances_from_not_started_through_review_to_complete() -> None:
    assert _stage(_project(), "package-intake").status is StageStatus.NOT_STARTED

    received = PackageIntakeFacts(
        releases_received=1,
        releases_incorporated=0,
        files_requiring_review=3,
        expected_missing=0,
        latest_release_id="REL-1",
    )
    in_progress = _project(received)
    stage = _stage(in_progress, "package-intake")
    assert stage.status is StageStatus.IN_PROGRESS
    assert in_progress.next_action_label == "Review received package files"
    assert in_progress.next_action_path.endswith("/releases/REL-1#files")

    reviewed = received.model_copy(update={"files_requiring_review": 0})
    done = _project(reviewed)
    assert _stage(done, "package-intake").status is StageStatus.COMPLETE
    # Controlled document review is the next action once the files are reviewed.
    assert done.next_action_stage_label == "Controlled customer documents"


def test_controlled_document_registration_starts_from_the_imported_file_context() -> None:
    reviewed = PackageIntakeFacts(
        releases_received=1,
        releases_incorporated=0,
        files_requiring_review=0,
        expected_missing=0,
        latest_release_id="REL-1",
    )
    projection = _project(reviewed)
    stage = _stage(projection, "documents")
    assert stage.action_label == "Register a controlled customer document"
    assert stage.action_path == f"/bids/{BID}/package-intake-addenda/releases/REL-1"


def test_expected_but_missing_release_blocks_package_intake() -> None:
    facts = PackageIntakeFacts(
        releases_received=0,
        releases_incorporated=0,
        files_requiring_review=0,
        expected_missing=1,
    )
    stage = _stage(_project(facts), "package-intake")
    assert stage.status is StageStatus.BLOCKED
    assert stage.blocked_reason is not None


def test_partially_populated_stage_is_never_labelled_not_started() -> None:
    projection = _project(requirements=2, responses=1)
    assert _stage(projection, "requirements").status is StageStatus.IN_PROGRESS


def test_handover_never_advertises_a_download_on_an_unready_bid() -> None:
    projection = _project()
    stage = _stage(projection, "handover")
    assert stage.status is StageStatus.BLOCKED
    assert stage.actionable is False
    assert "download" not in stage.action_label.casefold()
    assert "handover" not in projection.next_action_label.casefold()


def test_handover_is_ready_for_review_only_once_readiness_is_clear() -> None:
    complete = PackageIntakeFacts(
        releases_received=1,
        releases_incorporated=1,
        files_requiring_review=0,
        expected_missing=0,
        basis_published=True,
        latest_release_id="REL-1",
    )
    projection = _project(complete, verdict="clear")
    stage = _stage(projection, "handover")
    assert stage.status is StageStatus.READY_FOR_REVIEW
    assert stage.actionable is True
    assert stage.action_path == f"/bids/{BID}/handover"


def test_unincorporated_release_routes_to_the_bid_basis_not_to_package_intake() -> None:
    reviewed = PackageIntakeFacts(
        releases_received=1,
        releases_incorporated=0,
        files_requiring_review=0,
        expected_missing=0,
        latest_release_id="REL-1",
    )
    stage = _stage(_project(reviewed), "handover")
    assert stage.action_label == "Publish or update the Bid Basis"
    assert stage.action_path.endswith("#publish-basis")


def test_stopped_pursuit_marks_downstream_stages_not_applicable() -> None:
    projection = _project(bid_status="no_bid")
    assert _stage(projection, "setup").status is StageStatus.COMPLETE
    for key in ("package-intake", "requirements", "handover"):
        stage = _stage(projection, key)
        assert stage.status is StageStatus.NOT_APPLICABLE
        assert stage.actionable is False


def test_every_stage_uses_only_the_agreed_status_vocabulary() -> None:
    allowed = {
        "Not started",
        "In progress",
        "Blocked",
        "Ready for review",
        "Complete",
        "Not applicable",
    }
    for status in StageStatus:
        assert status.value in allowed
    for stage in _project().stages:
        assert stage.status.value in allowed


def test_status_and_readiness_lines_are_compact_and_name_their_source() -> None:
    outstanding = tuple(
        OutstandingItem(
            heading=f"Missing {index}",
            detail="d",
            consequence="c",
            owner="Jason",
            owing_party="Bid owner",
            due_date=date(2026, 10, 20),
            evidence_source="e",
            resolve_label="Resolve",
            resolve_path="/x",
            blocking=True,
        )
        for index in range(3)
    )
    projection = _project(outstanding=outstanding)
    assert projection.status_line == "Package intake and addenda — not started"
    assert projection.readiness_line == "Readiness: Hold — 3 prerequisites outstanding"
    assert projection.blocking_count == 3

    clear = _project(verdict="clear")
    assert clear.readiness_line == "Readiness: Clear — no prerequisite outstanding"


def test_every_stage_link_stays_inside_this_bid() -> None:
    projection = _project()
    for stage in projection.stages:
        assert stage.section_path.startswith(f"/bids/{BID}")
        assert BID in stage.action_path


def test_empty_bid_id_is_rejected_rather_than_producing_broken_links() -> None:
    with pytest.raises(ValueError, match="bid_id is required"):
        project_bid_workflow(
            "",
            bid_status="active",
            navigator=_navigator(),
            intake=NO_PACKAGE,
            readiness_verdict="hold",
            outstanding=(),
        )


def test_blocker_headings_state_what_is_missing_never_a_positive_requirement() -> None:
    headings = (
        *BLOCKER_HEADINGS.values(),
        *INTAKE_HEADINGS.values(),
        *PROPOSAL_HEADINGS.values(),
    )
    for heading in headings:
        assert not heading.endswith(".")
        assert " has been " not in heading
        assert " have been " not in heading
    assert blocker_heading("g1.bid_no_bid_approved", "x") == "Bid/no-bid approval missing"
    assert blocker_heading("g4.margin_approved", "x") == "Margin approval required"
    assert blocker_heading("g5.prior_gates_passed", "x") == "Earlier gate incomplete"
    assert intake_heading("RECEIVED_RELEASE_NOT_IN_BASIS", "x") == "Addendum not incorporated"
    assert proposal_heading("UNRESOLVED_REQUIREMENT", "x") == "Requirement response incomplete"
    assert blocker_heading("unknown.condition", "fallback") == "fallback"
    assert proposal_heading("UNKNOWN_CODE", "fallback") == "fallback"


def test_every_gate_condition_the_engine_can_emit_has_a_negative_heading(
    valid_bid,
) -> None:
    """No authoritative condition may fall back to its positive engine wording."""
    results = evaluate_all_gates(
        GateContext(
            bid=valid_bid,
            approvals=[],
            scope_items=[],
            high_severity_findings=[],
            unconfirmed_counts={},
            prior_gate_results={},
        )
    )
    emitted = {condition.condition_id for result in results for condition in result.conditions}
    assert emitted, "the gate engine emitted no conditions"
    assert emitted <= set(BLOCKER_HEADINGS), sorted(emitted - set(BLOCKER_HEADINGS))


def test_a_later_blocked_stage_does_not_advertise_an_action_out_of_sequence() -> None:
    """A failing gate on work not yet reached is orientation, not an invitation.

    The imported release makes stage 9 Blocked while stages 3-8 are untouched. The
    Bid Basis belongs to stage 9, so it must not compete with package review.
    """
    received = PackageIntakeFacts(
        releases_received=1,
        releases_incorporated=0,
        files_requiring_review=3,
        expected_missing=0,
    )
    projection = _project(received)
    handover = _stage(projection, "handover")
    assert handover.status is StageStatus.BLOCKED
    assert handover.actionable is False
    assert projection.current_stage_key == "package-intake"
    assert projection.next_action_label == "Review received package files"


def test_the_current_stage_still_advertises_its_action_when_blocked() -> None:
    """Suppression applies only ahead of the work, never at the current position."""
    expected_only = PackageIntakeFacts(
        releases_received=0,
        releases_incorporated=0,
        files_requiring_review=0,
        expected_missing=1,
    )
    projection = _project(expected_only)
    stage = _stage(projection, "package-intake")
    assert stage.status is StageStatus.BLOCKED
    assert stage.actionable is True
    assert projection.next_action_label == "Import the expected customer release"


def test_manufacturer_coverage_is_recorded_without_leaving_the_bid() -> None:
    stage = _stage(_project(), "manufacturers")
    assert stage.action_path.startswith(f"/bids/{BID}/")


def test_every_proposal_readiness_blocker_code_has_a_negative_heading() -> None:
    """The OPS-09 issue control must never fall back to a bare category name."""
    source = (
        pathlib.Path(__file__).parents[2] / "core" / "proposal_exchange_service.py"
    ).read_text()
    emitted = set(re.findall(r'^\s+"([A-Z][A-Z_]+)",$', source, re.M))
    emitted = {code for code in emitted if not code.startswith("GATE_")}
    assert emitted, "no proposal blocker codes were found"
    assert emitted <= set(PROPOSAL_HEADINGS), sorted(emitted - set(PROPOSAL_HEADINGS))
