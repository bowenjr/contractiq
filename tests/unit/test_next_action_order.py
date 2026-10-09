"""OPS-13A-F workflow ordering and shared next-action presentation."""

from __future__ import annotations

from datetime import UTC, date, datetime

from core.bid_control_center import (
    BidPortfolioFilters,
    BidPortfolioView,
    MyDayBidBucket,
    project_bid_portfolio,
    project_bid_workspace,
    workflow_development_blockers,
)
from core.bid_workflow import (
    BidWorkflowProjection,
    PackageIntakeFacts,
    StageStatus,
    project_bid_workflow,
)
from core.enums import BidStatus, Gate
from core.ops07w import build_navigator
from core.owner_decision_evidence import build_owner_decision_evidence_snapshot
from core.owner_decisions import OwnerDecisionValue
from core.readiness import Blocker, ReadinessReport, ReadinessVerdict
from core.schemas import Bid

BID_ID = "B-2026-0001"
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


def _evidence(*, reviewed: int, state: str | None = None, requirements: int = 0):
    job = None
    if state is not None:
        job = {
            "job_id": "JOB-1",
            "state": state,
            "files_total": 10,
            "files_done": 10,
            "files_failed": 2,
            "passages_total": 20,
            "passages_analysed": 12,
            "passages_failed": 8,
        }
    return build_owner_decision_evidence_snapshot(
        received_files_total=10,
        received_files_reviewed=reviewed,
        latest_discovery_job=job,
        discovery_partial_or_failed_files=2 if state == "PARTIAL" else 0,
        requirement_count=requirements,
        accepted_deliverable_count=0,
    )


def _workflow(
    *,
    unreviewed: int,
    evidence_state: str | None = None,
    requirements: int = 0,
    decision: OwnerDecisionValue | None = None,
    bid_status: str = "active",
    reason: str | None = None,
):
    return project_bid_workflow(
        BID_ID,
        bid_status=bid_status,
        navigator=build_navigator(BID_ID, COUNTS, "hold", "Jason"),
        intake=PackageIntakeFacts(
            releases_received=1,
            releases_incorporated=0,
            files_requiring_review=unreviewed,
            expected_missing=0,
            latest_release_id="REL-1",
            included_files=1,
            discovery_latest_state=evidence_state,
        ),
        readiness_verdict="hold",
        outstanding=(),
        owner_decision=decision,
        owner_decision_reason=reason,
        owner_decision_evidence=_evidence(
            reviewed=10 - unreviewed,
            state=evidence_state,
            requirements=requirements,
        ),
    )


def _readiness(bid_id: str) -> ReadinessReport:
    return ReadinessReport(
        bid_id=bid_id,
        verdict=ReadinessVerdict.HOLD,
        blockers=[
            Blocker(
                condition_id="g1.owner_pursuit_decided",
                gate=Gate.G1,
                description="The owner has decided to pursue this Bid.",
                detail="No current Pursue decision is recorded.",
                material=True,
            )
        ],
        generated_at=datetime(2026, 10, 1, tzinfo=UTC),
        summary="Hold",
        advisory="Deterministic test projection.",
    )


def test_g1_g2_g3_follow_review_assessment_decision_order(valid_bid: Bid) -> None:
    g1 = _workflow(unreviewed=10)
    assert g1.next_action_label == "Run deliverables discovery"

    report = _readiness(valid_bid.bid_id)
    workspace = project_bid_workspace(valid_bid, report, [], [], date(2026, 10, 1), workflow=g1)
    portfolio = project_bid_portfolio(
        [valid_bid],
        {valid_bid.bid_id: report},
        [],
        [],
        BidPortfolioFilters(),
        date(2026, 10, 1),
        workflow_by_bid={valid_bid.bid_id: g1},
    )
    assert (workspace.next_action, workspace.next_action_destination) == (
        portfolio.rows[0].next_action,
        portfolio.rows[0].next_action_destination,
    )
    assert workspace.next_action == "Run deliverables discovery"
    assert (
        workflow_development_blockers(
            workspace.blockers,
            package_received=True,
            evidence=_evidence(reviewed=0),
        )
        == []
    )

    g2 = _workflow(unreviewed=0)
    assert g2.next_action_label == "Run deliverables discovery"
    assert g2.next_action_path == f"/bids/{BID_ID}/deliverable-discovery"

    g3 = _workflow(unreviewed=0, evidence_state="PARTIAL")
    assert g3.next_action_label == "Record owner decision"
    assert g3.next_action_path == f"/bids/{BID_ID}/owner-decision"
    assert (
        workflow_development_blockers(
            workspace.blockers,
            package_received=True,
            evidence=_evidence(reviewed=10, state="PARTIAL"),
        )
        == workspace.blockers
    )


def test_pursue_completes_owner_stage_and_link_remains_renderable() -> None:
    projection = _workflow(
        unreviewed=0,
        requirements=1,
        decision=OwnerDecisionValue.PURSUE,
    )
    stage = next(item for item in projection.stages if item.key == "owner-decision")
    assert stage.status is StageStatus.COMPLETE
    assert stage.action_path == f"/bids/{BID_ID}/owner-decision"
    assert stage.section_path == f"/bids/{BID_ID}/owner-decision"
    assert projection.next_action_stage_label == "Prepare the Bid"


def test_g5_stopped_bid_uses_owner_action_and_history_bucket(valid_bid: Bid) -> None:
    stopped_bid = valid_bid.model_copy(update={"status": BidStatus.NO_BID})
    projection = _workflow(
        unreviewed=0,
        requirements=1,
        decision=OwnerDecisionValue.DO_NOT_PURSUE,
        bid_status="no_bid",
        reason="Commercial exposure is unacceptable",
    )
    assert projection.next_action_label == "Record a new decision"
    assert projection.stopped_reason == "Commercial exposure is unacceptable"
    assert all(
        stage.status is StageStatus.NOT_APPLICABLE
        for stage in projection.stages
        if stage.number > 3
    )

    report = _readiness(stopped_bid.bid_id)
    portfolio = project_bid_portfolio(
        [stopped_bid],
        {stopped_bid.bid_id: report},
        [],
        [],
        BidPortfolioFilters(view=BidPortfolioView.HISTORY),
        date(2026, 10, 1),
        workflow_by_bid={stopped_bid.bid_id: projection},
    )
    assert portfolio.rows[0].my_day_bucket is MyDayBidBucket.HISTORY
    assert portfolio.rows[0].next_action == "Record a new decision"
    assert portfolio.rows[0].stopped_reason == "Commercial exposure is unacceptable"


def test_legacy_submitted_bid_is_not_retroactively_sent_to_owner_decision() -> None:
    projection = _workflow(unreviewed=0, bid_status="submitted")
    stage = next(item for item in projection.stages if item.key == "owner-decision")
    assert stage.status is StageStatus.NOT_APPLICABLE
    assert not stage.actionable
    assert projection.next_action_label != "Record owner decision"


def test_portfolio_headline_hides_owner_decision_until_its_step_is_reached(
    valid_bid: Bid,
) -> None:
    """My Day must not headline the owner decision while files or assessment come first."""
    report = _readiness(valid_bid.bid_id)

    def headline(workflow: BidWorkflowProjection) -> str | None:
        portfolio = project_bid_portfolio(
            [valid_bid],
            {valid_bid.bid_id: report},
            [],
            [],
            BidPortfolioFilters(),
            date(2026, 10, 1),
            workflow_by_bid={valid_bid.bid_id: workflow},
        )
        blocker = portfolio.rows[0].highest_blocker
        return blocker.condition_id if blocker is not None else None

    assert headline(_workflow(unreviewed=10)) is None
    assert headline(_workflow(unreviewed=0)) is None
    assert headline(_workflow(unreviewed=0, evidence_state="PARTIAL")) == (
        "g1.owner_pursuit_decided"
    )
