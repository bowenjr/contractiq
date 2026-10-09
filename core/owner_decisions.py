"""Frozen domain evidence for an owner's pursuit decision."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.schemas import Provenance


class OwnerDecisionValue(StrEnum):
    """The two outcomes available to the accountable Bid owner."""

    PURSUE = "PURSUE"
    DO_NOT_PURSUE = "DO_NOT_PURSUE"


class ReceivedFileReviewSnapshot(BaseModel):
    """Frozen received-file review totals shown to the owner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total: int = Field(ge=0)
    reviewed: int = Field(ge=0)
    undecided: int = Field(ge=0)


class DiscoveryJobSnapshot(BaseModel):
    """Frozen status and coverage of the latest discovery job, when available."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    state: str
    files_total: int = Field(ge=0)
    files_done: int = Field(ge=0)
    files_failed: int = Field(ge=0)
    passages_total: int = Field(ge=0)
    passages_analysed: int = Field(ge=0)
    passages_failed: int = Field(ge=0)
    partial_or_failed_files: int = Field(ge=0)


class OwnerDecisionEvidenceSnapshot(BaseModel):
    """Frozen summary of the evidence available when an owner decided."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    received_files: ReceivedFileReviewSnapshot
    latest_discovery_job: DiscoveryJobSnapshot | None = None
    requirement_count: int = Field(ge=0)
    accepted_deliverable_count: int = Field(ge=0)
    unresolved_questions: tuple[str, ...] = ()


class OwnerDecision(BaseModel):
    """One immutable owner pursuit decision in an append-only lineage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str
    bid_id: str
    decision: OwnerDecisionValue
    reason: str = Field(min_length=1, max_length=500)
    decided_by: str = Field(min_length=1)
    decided_at: datetime
    supersedes_decision_id: str | None = None
    evidence_snapshot: OwnerDecisionEvidenceSnapshot
    provenance: Provenance

    @field_validator("reason", "decided_by", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        """Strip user-entered required text before length validation."""
        return value.strip() if isinstance(value, str) else value


__all__ = [
    "DiscoveryJobSnapshot",
    "OwnerDecision",
    "OwnerDecisionEvidenceSnapshot",
    "OwnerDecisionValue",
    "ReceivedFileReviewSnapshot",
]
