"""Canonical readiness-condition phase assignments."""

from enum import StrEnum


class BlockerPhase(StrEnum):
    """The action boundary at which a readiness blocker applies."""

    DEVELOPMENT = "development"
    ISSUE = "issue"


ISSUE_PHASE_CONDITION_IDS = frozenset(
    {
        "g4.margin_approved",
        "g4.required_approvals",
        "g4.high_findings_have_authority",
        "g6.senior_approvals_current",
    }
)


def condition_phase(condition_id: str) -> BlockerPhase:
    """Return the fixed phase for one deterministic condition identifier."""
    if condition_id in ISSUE_PHASE_CONDITION_IDS:
        return BlockerPhase.ISSUE
    return BlockerPhase.DEVELOPMENT


__all__ = ["BlockerPhase", "ISSUE_PHASE_CONDITION_IDS", "condition_phase"]
