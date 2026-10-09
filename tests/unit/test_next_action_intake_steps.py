"""My Day and the Bid workspace share every intake next-action step."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from core.bid_control_center import (
    BidPortfolioFilters,
    outstanding_items,
    project_bid_portfolio,
    project_bid_workspace,
)
from core.bid_workflow import PackageIntakeFacts, project_bid_workflow
from core.ops07w import build_navigator
from core.owner_decisions import OwnerDecisionValue
from core.schemas import Bid
from tests.unit.test_next_action_order import BID_ID, COUNTS, _evidence, _readiness


def _projection(
    *,
    unreviewed: int,
    assessment: bool,
    decision: bool,
    governing: int | None = None,
    pending_control: int | None = None,
    controlled_workflow: bool = False,
):
    counts = dict(COUNTS)
    if controlled_workflow:
        counts.update(
            {
                "documents": 1,
                "requirements": 1,
                "responses": 1,
                "scopes": 1,
                "interfaces": 1,
                "packages": 1,
                "manufacturer_associations": 1,
                "manufacturer_requirements": 1,
                "manufacturer_complete": 1,
                "commercial": 1,
                "risks": 1,
                "decisions": 1,
                "approvals": 1,
                "proposals": 1,
                "deliverables": 1,
            }
        )
    return project_bid_workflow(
        BID_ID,
        bid_status="active",
        navigator=build_navigator(BID_ID, counts, "hold", "Jason"),
        intake=PackageIntakeFacts(
            releases_received=1,
            releases_incorporated=0,
            files_requiring_review=unreviewed,
            expected_missing=0,
            latest_release_id="REL-1",
            governing_sources_selected=governing,
            governing_sources_pending_control=pending_control,
            included_files=1,
            discovery_latest_state="SUCCEEDED" if assessment else None,
        ),
        readiness_verdict="hold",
        outstanding=(),
        owner_decision=OwnerDecisionValue.PURSUE if decision else None,
        owner_decision_evidence=_evidence(
            reviewed=10 - unreviewed,
            state="SUCCEEDED" if assessment else None,
        ),
    )


def test_workspace_and_my_day_match_across_every_intake_step(valid_bid: Bid) -> None:
    release = f"/bids/{BID_ID}/package-intake-addenda/releases/REL-1"
    cases = (
        (
            _projection(unreviewed=10, assessment=False, decision=False),
            "Run deliverables discovery",
            f"/bids/{BID_ID}/deliverable-discovery",
        ),
        (
            _projection(unreviewed=0, assessment=False, decision=False),
            "Run deliverables discovery",
            f"/bids/{BID_ID}/deliverable-discovery",
        ),
        (
            _projection(unreviewed=0, assessment=True, decision=False),
            "Record owner decision",
            f"/bids/{BID_ID}/owner-decision",
        ),
        (
            _projection(
                unreviewed=0,
                assessment=True,
                decision=True,
                governing=0,
                pending_control=0,
            ),
            "Choose governing sources",
            f"{release}#governing-sources",
        ),
        (
            _projection(
                unreviewed=0,
                assessment=True,
                decision=True,
                governing=2,
                pending_control=2,
            ),
            "Put files under document control",
            f"{release}#document-control",
        ),
        (
            _projection(
                unreviewed=0,
                assessment=True,
                decision=True,
                governing=2,
                pending_control=0,
                controlled_workflow=True,
            ),
            "Prepare the proposal and deliverables",
            f"/proposals?bid_id={BID_ID}#author",
        ),
    )
    report = _readiness(valid_bid.bid_id)
    for workflow, expected_text, expected_destination in cases:
        workspace = project_bid_workspace(
            valid_bid,
            report,
            [],
            [],
            date(2026, 10, 1),
            workflow=workflow,
        )
        portfolio = project_bid_portfolio(
            [valid_bid],
            {valid_bid.bid_id: report},
            [],
            [],
            BidPortfolioFilters(),
            date(2026, 10, 1),
            workflow_by_bid={valid_bid.bid_id: workflow},
        )
        assert (workspace.next_action, workspace.next_action_destination) == (
            portfolio.rows[0].next_action,
            portfolio.rows[0].next_action_destination,
        )
        assert (workspace.next_action, workspace.next_action_destination) == (
            expected_text,
            expected_destination,
        )


def test_bid_basis_resolve_link_names_the_first_usable_control() -> None:
    attention = SimpleNamespace(
        code="RECEIVED_RELEASE_NOT_IN_BASIS",
        message="Release is not in the Bid Basis.",
        destination=f"/bids/{BID_ID}/package-intake-addenda/releases/REL-1#files",
        blocking=True,
        release_type="INITIAL_PACKAGE",
    )
    states = (
        (3, 0, 0, "Review received package files", "#files"),
        (0, 0, 0, "Choose governing sources", "#governing-sources"),
        (0, 2, 2, "Put files under document control", "#document-control"),
        (0, 2, 0, "Confirm Bid Basis", "#publish-basis"),
    )
    for unreviewed, governing, pending, expected_label, expected_anchor in states:
        items = outstanding_items(
            (),
            intake_attention=(attention,),
            bid_id=BID_ID,
            intake=PackageIntakeFacts(
                releases_received=1,
                releases_incorporated=0,
                files_requiring_review=unreviewed,
                expected_missing=0,
                latest_release_id="REL-1",
                governing_sources_selected=governing,
                governing_sources_pending_control=pending,
            ),
        )
        assert items[0].resolve_label == expected_label
        assert items[0].resolve_path.endswith(expected_anchor)
