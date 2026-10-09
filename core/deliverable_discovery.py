"""OPS-13A — Bid deliverables discovery: candidate proposals and pure rules.

Non-authoritative evidence that feeds the authoritative `core.deliverables.Deliverable`
register on human confirmation. See docs/tasks/OPS-13A-bid-deliverables-discovery.md.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.date_integrity import DueDateBasis
from core.requirement_lifecycle import (
    AppliesAt,
    DestinationKind,
    DiscoveryLifecycle,
    ProposalClassification,
)


class LocatorType(StrEnum):
    PAGE = "PAGE"
    PARAGRAPH = "PARAGRAPH"
    SHEET_CELL = "SHEET_CELL"


class ExtractionStatus(StrEnum):
    OK = "OK"
    UNREADABLE = "UNREADABLE"


class DiscoveryCriticality(StrEnum):
    REQUIRED = "REQUIRED"
    CONDITIONAL = "CONDITIONAL"
    UNCLEAR = "UNCLEAR"


class ProposalReviewDecision(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"


class RunStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ExtractionUnit(BaseModel):
    """One deterministically extracted, located passage from a managed document copy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    extraction_id: str = Field(default_factory=lambda: f"DDX-{uuid4().hex}")
    bid_id: str
    run_id: str
    document_version_id: str | None = Field(default=None, min_length=1)
    received_file_id: str | None = Field(default=None, min_length=1)
    locator_type: LocatorType
    locator: str = Field(min_length=1, max_length=200)
    text_excerpt: str = Field(max_length=4000)
    char_count: int = Field(ge=0)
    status: ExtractionStatus
    failure_reason: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def semantic(self) -> Self:
        if (self.document_version_id is None) == (self.received_file_id is None):
            raise ValueError(
                "extraction unit must identify exactly one of document_version_id or "
                "received_file_id"
            )
        if self.status is ExtractionStatus.UNREADABLE and not self.failure_reason:
            raise ValueError("unreadable extraction unit requires failure_reason")
        if self.status is ExtractionStatus.OK and not self.text_excerpt.strip():
            raise ValueError("readable extraction unit requires non-empty text_excerpt")
        return self


class SourceCitation(BaseModel):
    """One source mention backing a consolidated proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_id: str = Field(default_factory=lambda: f"DDC-{uuid4().hex}")
    document_version_id: str | None = Field(default=None, min_length=1)
    received_file_id: str | None = Field(default=None, min_length=1)
    locator_type: LocatorType
    locator: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(max_length=2000)
    quote: str | None = Field(default=None, max_length=2000)
    """The sentence that creates the obligation, verified to occur word for word in the passage."""

    @model_validator(mode="after")
    def source_identity(self) -> Self:
        if (self.document_version_id is None) == (self.received_file_id is None):
            raise ValueError(
                "source citation must identify exactly one of document_version_id or "
                "received_file_id"
            )
        return self


class DeliverableProposal(BaseModel):
    """One consolidated, human-reviewable candidate Bid submission deliverable."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(default_factory=lambda: f"DDP-{uuid4().hex}")
    bid_id: str
    run_id: str
    title: str = Field(min_length=1, max_length=300)
    category: str = Field(min_length=1, max_length=100)
    criticality: DiscoveryCriticality
    condition_text: str | None = None
    due_date: date | None = None
    """Set only when the date is stated in the document or calculated from a recorded trigger."""
    due_date_basis: DueDateBasis = DueDateBasis.UNKNOWN
    proposed_due_date: date | None = None
    """A date the model suggested that nothing verifies. A person must confirm it."""
    due_date_text: str | None = None
    """The customer's wording of the date or timing rule, verified to occur in the source."""
    requested_format: str | None = None
    requested_recipient: str | None = None
    proposed_owner: str | None = None
    classification: ProposalClassification | None = None
    sources: tuple[SourceCitation, ...] = Field(min_length=1)
    dedup_key: str
    created_at: datetime

    @model_validator(mode="after")
    def semantic(self) -> Self:
        if self.criticality is DiscoveryCriticality.CONDITIONAL and not self.condition_text:
            raise ValueError("conditional proposal requires condition_text")
        return self


class ProposalReviewEvent(BaseModel):
    """Append-only human decision on a proposal. Never mutates the proposal itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(default_factory=lambda: f"DDR-{uuid4().hex}")
    proposal_id: str
    bid_id: str
    decision: ProposalReviewDecision
    reviewer: str = Field(min_length=1)
    rationale: str | None = None
    linked_deliverable_id: str | None = None
    destination_kind: DestinationKind | None = None
    destination_record_id: str | None = None
    supersedes_event_id: str | None = None
    reviewed_at: datetime

    @model_validator(mode="after")
    def semantic(self) -> Self:
        if self.decision is ProposalReviewDecision.ACCEPTED:
            if self.destination_kind in {None, DestinationKind.DELIVERABLE}:
                if not self.linked_deliverable_id:
                    raise ValueError("accepted deliverable decision requires linked_deliverable_id")
            elif not self.destination_record_id:
                raise ValueError("accepted non-deliverable decision requires destination_record_id")
        elif self.linked_deliverable_id or self.destination_kind or self.destination_record_id:
            raise ValueError("only an accepted decision may carry a destination")
        if (
            self.decision
            in (
                ProposalReviewDecision.REJECTED,
                ProposalReviewDecision.DEFERRED,
            )
            and not self.rationale
        ):
            raise ValueError("rejection or deferral requires rationale")
        return self


class SourcePromotion(BaseModel):
    """Append-only association of a received-file citation to a controlled version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    promotion_id: str = Field(default_factory=lambda: f"DSP-{uuid4().hex}")
    bid_id: str
    deliverable_id: str
    proposal_id: str
    citation_id: str
    received_file_id: str
    document_version_id: str
    file_document_link_id: str
    link_relationship: str
    rationale: str = Field(min_length=1)
    operation_id: str = Field(min_length=1, max_length=200)
    promoted_by: str = Field(min_length=1)
    promoted_at: datetime


class AddendumDiscoveryFlag(BaseModel):
    """Append-only: an addendum directive potentially affects a proposal or deliverable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    flag_id: str = Field(default_factory=lambda: f"DDF-{uuid4().hex}")
    bid_id: str
    directive_id: str
    proposal_id: str | None = None
    deliverable_id: str | None = None
    rationale: str = Field(min_length=1)
    supersedes_flag_id: str | None = None
    created_at: datetime
    created_by: str

    @model_validator(mode="after")
    def semantic(self) -> Self:
        if bool(self.proposal_id) == bool(self.deliverable_id):
            raise ValueError("flag must target exactly one of proposal_id or deliverable_id")
        return self


class DiscoveryRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(default_factory=lambda: f"DDN-{uuid4().hex}")
    bid_id: str
    snapshot_id: str | None = None
    operation_id: str = Field(min_length=1, max_length=200)
    status: RunStatus
    proposal_count: int = Field(ge=0)
    rejected_source_count: int = Field(ge=0, default=0)
    actor: str
    started_at: datetime
    completed_at: datetime


_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def normalize_dedup_key(
    bid_id: str,
    title: str,
    category: str,
    lifecycle: DiscoveryLifecycle | ProposalClassification | str | None = None,
    applies_at: AppliesAt | str | None = None,
) -> str:
    """Consolidation key for duplicate mentions of the same obligation.

    Two proposals with the same normalized title+category within a Bid are the same
    obligation seen more than once (e.g. mentioned in both the instructions and the
    specification) and must be merged into one proposal with multiple source citations,
    not duplicated as two proposals.
    """
    normalized_title = _NORMALIZE_RE.sub("-", title.strip().lower()).strip("-")
    if lifecycle is not None:
        if isinstance(lifecycle, ProposalClassification):
            lifecycle_value = lifecycle.lifecycle
            effective_applies_at = lifecycle.applies_at
        else:
            lifecycle_value = DiscoveryLifecycle(lifecycle)
            effective_applies_at = AppliesAt(applies_at) if applies_at is not None else None
        bucket = lifecycle_value.value.lower()
        if lifecycle_value is DiscoveryLifecycle.CONDITIONAL:
            suffix = effective_applies_at.value.lower() if effective_applies_at else "unset"
            bucket = f"conditional-{suffix}"
        return f"{bid_id}:{bucket}:{normalized_title}"
    normalized_category = _NORMALIZE_RE.sub("-", category.strip().lower()).strip("-")
    return f"{bid_id}:{normalized_category}:{normalized_title}"


def is_unambiguous(proposal: DeliverableProposal) -> bool:
    """Whether a proposal is safe for bulk (unreviewed-detail) acceptance.

    Unclear criticality always requires a human decision. A conditional proposal without
    condition_text cannot occur (enforced by the model validator) but is defensively
    excluded here too.
    """
    if proposal.criticality is DiscoveryCriticality.UNCLEAR:
        return False
    if proposal.criticality is DiscoveryCriticality.CONDITIONAL and not proposal.condition_text:
        return False
    return True
