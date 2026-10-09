"""Stage 2 — turn one model (or rule) requirement item into a guarded, classified candidate.

The model proposes; ``core.requirement_lifecycle.guard_classification`` decides what is kept. A
guard never drops an item: an unverifiable classification is downgraded to NEEDS_REVIEW and the
finding stays visible to the reviewer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.deliverable_discovery import DiscoveryCriticality
from core.requirement_lifecycle import (
    AppliesAt,
    ClassifiedBy,
    Confidence,
    DiscoveryLifecycle,
    ItemKind,
    RequirementStatus,
    guard_classification,
)


class RawRequirement(BaseModel):
    """One item of the Stage 2 reply schema (see ``CANDIDATE_RESPONSE_SCHEMA``)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    lifecycle: DiscoveryLifecycle
    requirement_status: RequirementStatus
    item_kind: ItemKind
    category: str = Field(min_length=1, max_length=100)
    condition_text: str | None = None
    applies_at: AppliesAt | None = None
    responsible_party: str | None = None
    timing_text: str | None = Field(default=None, max_length=1000)
    due_date: str | None = None
    dependencies: str | None = None
    confidence: Confidence
    review_reason: str | None = None
    source_section: str | None = None
    source_quote: str | None = Field(default=None, max_length=2000)


def legacy_criticality(
    status: RequirementStatus, condition_text: str | None
) -> DiscoveryCriticality:
    """The legacy ``criticality`` column value for a classified finding."""
    if status is RequirementStatus.REQUIRED:
        return DiscoveryCriticality.REQUIRED
    if status is RequirementStatus.CONDITIONAL and (condition_text or "").strip():
        return DiscoveryCriticality.CONDITIONAL
    return DiscoveryCriticality.UNCLEAR


def parse_requirement(
    item: dict[str, Any], passage_text: str, classified_by: ClassifiedBy
) -> tuple[RawRequirement, Any]:
    """Validate ``item`` and return it with its guarded ``ProposalClassification``.

    Raises ``pydantic.ValidationError`` or ``ValueError`` when the item is unusable.
    """
    raw = RawRequirement.model_validate(item)
    if raw.due_date:
        datetime.strptime(raw.due_date, "%Y-%m-%d")  # noqa: DTZ007 - date only, validated
    classification = guard_classification(
        {**raw.model_dump(mode="json"), "description": raw.description[:300]},
        passage_text,
        classified_by=classified_by,
    )
    return raw, classification
