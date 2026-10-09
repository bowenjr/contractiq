"""Handover stays not-ready while issue-phase senior approvals are pending (OPS-13A-F)."""

from datetime import UTC, datetime

from core.bid_control_center import handover_gate_blockers
from core.enums import Gate
from core.readiness import Blocker, ReadinessReport, ReadinessVerdict
from core.readiness_phases import BlockerPhase


def _report(*issue: Blocker) -> ReadinessReport:
    return ReadinessReport(
        bid_id="B-2026-0001",
        verdict=ReadinessVerdict.CLEAR,
        issue_blockers=list(issue),
        generated_at=datetime.now(UTC),
        summary="Bid is CLEAR: no material blockers remain.",
        advisory="",
    )


def _issue_blocker(*, overridden: bool = False) -> Blocker:
    return Blocker(
        condition_id="g4.margin_approved",
        gate=Gate.G4,
        description="Required margin approval has been obtained.",
        detail="MARGIN approval is required for Level 2 or higher and has not been obtained.",
        material=True,
        phase=BlockerPhase.ISSUE,
        overridden=overridden,
    )


def test_pending_issue_phase_approval_reaches_handover_although_verdict_is_clear() -> None:
    report = _report(_issue_blocker())

    lines = handover_gate_blockers([], report)

    assert report.verdict is ReadinessVerdict.CLEAR
    assert len(lines) == 1
    assert lines[0].startswith("Pending before issue")
    assert "MARGIN approval is required" in lines[0]


def test_no_issue_phase_blockers_adds_nothing_to_handover() -> None:
    assert handover_gate_blockers([], _report()) == []
