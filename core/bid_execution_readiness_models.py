"""Frozen contracts for deterministic Bid execution readiness."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from core.bid_execution_approvals import ApprovalRequirementFact
from core.bid_execution_projection import ExecutionItem


class ReadinessSeverity(StrEnum):
    """Business impact of one submission-readiness finding."""

    BLOCKING = "BLOCKING"
    WARNING = "WARNING"
    INFO = "INFO"


class ExecutionReadinessVerdict(StrEnum):
    """Advisory submission-review verdict; it grants no authority."""

    NOT_READY = "NOT_READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"

    @property
    def label(self) -> str:
        """Return the business-facing verdict label."""
        return {
            self.NOT_READY: "Not ready",
            self.READY_WITH_WARNINGS: "Ready with warnings",
            self.READY_FOR_REVIEW: "Ready for review",
        }[self]


class ReadinessFinding(BaseModel):
    """One deterministic readiness issue or informational count."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: ReadinessSeverity
    code: str
    headline: str
    count: int = Field(ge=1)
    detail: str
    items: list[tuple[str, str]] = Field(default_factory=list)
    resolve_label: str
    resolve_url: str


class ReadinessFacts(BaseModel):
    """All projected and governance facts consumed by the pure evaluator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bid_id: str
    returnables: list[ExecutionItem] = Field(default_factory=list)
    manufacturer_inputs: list[ExecutionItem] = Field(default_factory=list)
    actions: list[ExecutionItem] = Field(default_factory=list)
    commercial: list[ExecutionItem] = Field(default_factory=list)
    post_award_deliverable_count: int = Field(default=0, ge=0)
    non_blocking_returnable_count: int = Field(default=0, ge=0)
    approvals: list[ApprovalRequirementFact] = Field(default_factory=list)


class ExecutionReadiness(BaseModel):
    """Sorted advisory readiness result for a single Bid."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bid_id: str
    verdict: ExecutionReadinessVerdict
    findings: list[ReadinessFinding] = Field(default_factory=list)
    blocking_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    info_count: int = Field(ge=0)
    approvals: list[ApprovalRequirementFact] = Field(default_factory=list)
    advisory: str
    as_of: date

    @property
    def verdict_label(self) -> str:
        """Return the business-facing label for the advisory verdict."""
        return self.verdict.label


__all__ = [
    "ApprovalRequirementFact",
    "ExecutionReadiness",
    "ExecutionReadinessVerdict",
    "ReadinessFacts",
    "ReadinessFinding",
    "ReadinessSeverity",
]
