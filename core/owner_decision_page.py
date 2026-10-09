"""Pure template-context assembly for the Bid owner decision page."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.enums import ApprovalType
from core.owner_decision_evidence import initial_requirements_assessment_available
from core.owner_decisions import (
    OwnerDecision,
    OwnerDecisionEvidenceSnapshot,
    OwnerDecisionValue,
)
from core.schemas import Approval, Bid

_LEGACY_LABEL = "Legacy approval record — not an owner decision"
_HISTORY_DECISION_LABELS = {
    OwnerDecisionValue.PURSUE: "Pursue",
    OwnerDecisionValue.DO_NOT_PURSUE: "Do not pursue",
}


def _state_label(decision: OwnerDecisionValue | None) -> str:
    """Return the owner decision state label shown at the top of the page."""
    if decision is None:
        return "No owner decision recorded"
    if decision is OwnerDecisionValue.PURSUE:
        return "Pursue"
    return "Do not pursue — Bid stopped"


def _format_when(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC")


def build_owner_decision_page(
    bid: Bid,
    current: OwnerDecision | None,
    history: list[OwnerDecision],
    evidence: OwnerDecisionEvidenceSnapshot,
    legacy_approvals: list[Approval],
    *,
    error: str | None = None,
    entered: dict[str, str] | None = None,
    recorded: bool = False,
) -> dict[str, Any]:
    """Assemble the owner decision page context with every label precomputed.

    The template renders this dict directly: it contains no logic of its own
    beyond iteration, so the state label, evidence rows, history rows (newest
    first) and legacy rows are all final here.
    """
    decision = current.decision if current is not None else None
    files = evidence.received_files
    job = evidence.latest_discovery_job
    evidence_rows: list[tuple[str, str]] = [
        ("Received files reviewed", f"{files.reviewed} of {files.total}"),
    ]
    if job is not None:
        evidence_rows.append(
            (
                "Latest discovery job",
                f"{job.state}: {job.files_done} of {job.files_total} files, "
                f"{job.partial_or_failed_files} partial or failed",
            )
        )
    else:
        evidence_rows.append(("Latest discovery job", "No discovery run recorded"))
    evidence_rows.append(("Customer requirements", str(evidence.requirement_count)))
    evidence_rows.append(("Accepted deliverables", str(evidence.accepted_deliverable_count)))
    evidence_rows.append(
        (
            "Initial requirements assessment",
            "Available" if initial_requirements_assessment_available(evidence) else "Not available",
        )
    )
    history_rows = [
        {
            "when": _format_when(row.decided_at),
            "decision": _HISTORY_DECISION_LABELS[row.decision],
            "reason": row.reason,
            "by": row.decided_by,
        }
        for row in reversed(history)
    ]
    legacy_rows = [
        {
            "label": _LEGACY_LABEL,
            "obtained": approval.obtained,
            "decision": approval.decision or "Not recorded",
            "recorded_at": (
                _format_when(approval.recorded_at)
                if approval.recorded_at
                else "Before dated records"
            ),
            "approval_id": approval.approval_id,
        }
        for approval in legacy_approvals
        if approval.approval_type is ApprovalType.BID_NO_BID
    ]
    return {
        "bid": bid,
        "state_label": _state_label(decision),
        "is_stopped": decision is OwnerDecisionValue.DO_NOT_PURSUE,
        "evidence_rows": evidence_rows,
        "unresolved_questions": list(evidence.unresolved_questions),
        "history_rows": history_rows,
        "legacy_rows": legacy_rows,
        "expected_current_decision_id": current.decision_id if current is not None else "",
        "error": error,
        "entered": entered,
        "recorded": recorded,
    }


__all__ = ["build_owner_decision_page"]
