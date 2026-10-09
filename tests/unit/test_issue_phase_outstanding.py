"""OPS-13A-F issue-phase presentation checks."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from core.bid_control_center import outstanding_items, project_bid_workspace
from core.bid_workflow import proposal_resolve_label
from core.enums import BidLevel, Gate
from core.readiness import Blocker, ReadinessReport, ReadinessVerdict
from core.schemas import Bid
from tests.unit.test_issue_phase_approvals import _level, _pursue
from tests.unit.test_ops09 import BID_ID, service_with_ready_bid


def _issue_report(bid_id: str) -> ReadinessReport:
    return ReadinessReport(
        bid_id=bid_id,
        verdict=ReadinessVerdict.CLEAR,
        blockers=[],
        issue_blockers=[
            Blocker(
                condition_id="g4.margin_approved",
                gate=Gate.G4,
                description="Margin approval is obtained when required.",
                detail="No current margin approval exists.",
                material=True,
            ),
            Blocker(
                condition_id="g6.senior_approvals_current",
                gate=Gate.G6,
                description="Required senior approvals are current.",
                detail="Required senior approval evidence is incomplete.",
                material=True,
            ),
        ],
        generated_at=datetime(2026, 10, 1, tzinfo=UTC),
        summary="Clear for drafting",
        advisory="Issue approvals remain pending.",
    )


def test_issue_phase_items_are_nonblocking_and_use_direct_approval_links(
    valid_bid: Bid,
) -> None:
    workspace = project_bid_workspace(
        valid_bid, _issue_report(valid_bid.bid_id), [], [], date(2026, 10, 1)
    )
    items = outstanding_items(workspace.blockers, issue_blockers=workspace.issue_blockers)

    assert workspace.blockers == []
    assert len(workspace.issue_blockers) == 2
    assert all(not item.blocking for item in items)
    assert all(item.scope == "pending_before_issue" for item in items)
    margin = next(item for item in items if item.heading == "Margin approval required")
    assert margin.resolve_label == "Record margin approval"
    assert margin.resolve_path.endswith("approval_type=MARGIN#gate-approval")
    senior = next(item for item in items if item.heading == "Senior approval incomplete")
    assert senior.resolve_label == "Record senior approval"
    assert senior.resolve_path.endswith("#gate-approval")


def test_draft_assessment_renders_pending_before_issue_notice(tmp_path: Path) -> None:
    service, repository = service_with_ready_bid(tmp_path)
    _level(service, repository, BidLevel.LEVEL_3)
    _pursue(service)
    control = service.assess(BID_ID)
    assert control.pending_before_issue

    template_root = Path(__file__).parents[2] / "templates"
    environment = Environment(loader=FileSystemLoader(template_root))
    # The app registers the production helper on its own environment; register the same one.
    environment.globals["proposal_resolve_label"] = proposal_resolve_label
    template = environment.get_template("proposal_issue_control.html")
    bid = repository.get_bid(BID_ID)
    assert bid is not None
    page = template.render(
        bid=bid,
        control=control,
        history=service.history(BID_ID),
        workspace=None,
        form_error=None,
        entered={},
        now_local="2026-10-01T12:00",
    )
    assert "Draft only — pending before issue:" in page
    assert "; ".join(control.pending_before_issue) in page
    assert control.blockers
    assert "Correct this authoritative fact" not in page
    for blocker in control.blockers:
        assert proposal_resolve_label(blocker.destination, blocker.code) in page


def test_bid_template_labels_pending_scope_before_issue() -> None:
    source = (Path(__file__).parents[2] / "templates" / "bid_detail.html").read_text()
    assert "pending before issue" in source
    assert "Before issue" in source
