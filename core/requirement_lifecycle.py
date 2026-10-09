"""Pure lifecycle vocabulary and deterministic guards for discovery findings.

This module deliberately contains no persistence or model calls.  Model output is evidence,
not control logic: every lifecycle used by the application is validated and guarded here before
the persistence layer can record it.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.date_integrity import locate_quote
from core.deliverables import DeliverableCriticality, LifecyclePhase
from core.ops08 import STANDARD_TOPICS


class DiscoveryLifecycle(StrEnum):
    PRE_BID_ACTION = "PRE_BID_ACTION"
    BID_RETURNABLE = "BID_RETURNABLE"
    MANUFACTURER_INPUT = "MANUFACTURER_INPUT"
    POST_AWARD = "POST_AWARD"
    CONDITIONAL = "CONDITIONAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class RequirementStatus(StrEnum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    CONDITIONAL = "CONDITIONAL"
    UNCLEAR = "UNCLEAR"


class ItemKind(StrEnum):
    SUBMISSION_DOCUMENT = "SUBMISSION_DOCUMENT"
    MANUFACTURER_DOCUMENT = "MANUFACTURER_DOCUMENT"
    MANUFACTURER_DATA = "MANUFACTURER_DATA"
    COMMERCIAL_QUALIFICATION = "COMMERCIAL_QUALIFICATION"
    CLARIFICATION = "CLARIFICATION"
    ACTIVITY = "ACTIVITY"
    OTHER = "OTHER"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class AppliesAt(StrEnum):
    WITH_BID = "WITH_BID"
    POST_AWARD = "POST_AWARD"


class DestinationKind(StrEnum):
    DELIVERABLE = "DELIVERABLE"
    VENDOR_DOCUMENT = "VENDOR_DOCUMENT"
    COMMERCIAL_POSITION = "COMMERCIAL_POSITION"
    WORK_ITEM = "WORK_ITEM"


class ClassifiedBy(StrEnum):
    MODEL = "MODEL"
    RULE = "RULE"


_STANDARD_TOPIC_KEYS = frozenset(topic[0] for topic in STANDARD_TOPICS)


class ProposalClassification(BaseModel):
    """Immutable lifecycle facts recorded beside an immutable discovery proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lifecycle: DiscoveryLifecycle
    model_lifecycle: DiscoveryLifecycle
    requirement_status: RequirementStatus
    item_kind: ItemKind
    description: str = Field(max_length=300)
    applies_at: AppliesAt | None = None
    commercial_topic: str | None = None
    responsible_party: str | None = None
    dependencies: str | None = None
    confidence: Confidence
    review_reason: str | None = None
    source_section: str | None = None
    classified_by: ClassifiedBy

    @model_validator(mode="after")
    def semantic(self) -> Self:
        if self.lifecycle is not DiscoveryLifecycle.CONDITIONAL and self.applies_at is not None:
            raise ValueError("applies_at is only valid for a CONDITIONAL lifecycle")
        if (
            self.lifecycle is DiscoveryLifecycle.NEEDS_REVIEW
            and not (self.review_reason or "").strip()
        ):
            raise ValueError("NEEDS_REVIEW classification requires review_reason")
        if self.item_kind is not ItemKind.COMMERCIAL_QUALIFICATION:
            if self.commercial_topic is not None:
                raise ValueError(
                    "commercial_topic is only valid for COMMERCIAL_QUALIFICATION items"
                )
        elif (
            self.commercial_topic is not None and self.commercial_topic not in _STANDARD_TOPIC_KEYS
        ):
            raise ValueError("commercial_topic must be a standard commercial topic key")
        return self


def destination_for(classification: ProposalClassification) -> DestinationKind | None:
    """Return the existing register that owns an acceptable classified finding."""
    if classification.lifecycle in {
        DiscoveryLifecycle.NOT_APPLICABLE,
        DiscoveryLifecycle.NEEDS_REVIEW,
    }:
        return None
    if classification.item_kind is ItemKind.COMMERCIAL_QUALIFICATION:
        return DestinationKind.COMMERCIAL_POSITION
    if classification.lifecycle is DiscoveryLifecycle.PRE_BID_ACTION:
        return DestinationKind.WORK_ITEM
    if classification.lifecycle is DiscoveryLifecycle.MANUFACTURER_INPUT:
        return DestinationKind.VENDOR_DOCUMENT
    if classification.lifecycle in {
        DiscoveryLifecycle.BID_RETURNABLE,
        DiscoveryLifecycle.POST_AWARD,
    }:
        return DestinationKind.DELIVERABLE
    if classification.lifecycle is DiscoveryLifecycle.CONDITIONAL:
        return DestinationKind.DELIVERABLE if classification.applies_at is not None else None
    return None


def deliverable_phase(classification: ProposalClassification) -> LifecyclePhase:
    """Map a deliverable-bound classification to an existing lifecycle phase."""
    if classification.lifecycle is DiscoveryLifecycle.BID_RETURNABLE:
        return LifecyclePhase.WITH_BID
    if classification.lifecycle is DiscoveryLifecycle.POST_AWARD:
        return LifecyclePhase.POST_AWARD
    if classification.lifecycle is DiscoveryLifecycle.CONDITIONAL:
        if classification.applies_at is AppliesAt.WITH_BID:
            return LifecyclePhase.WITH_BID
        if classification.applies_at is AppliesAt.POST_AWARD:
            return LifecyclePhase.POST_AWARD
        raise ValueError("Choose whether the conditional item applies with the bid or after award")
    raise ValueError("This lifecycle does not route to the deliverables register")


def deliverable_criticality(
    classification: ProposalClassification,
) -> DeliverableCriticality | None:
    """Map requirement status without inventing a choice for UNCLEAR findings."""
    return {
        RequirementStatus.REQUIRED: DeliverableCriticality.MANDATORY,
        RequirementStatus.OPTIONAL: DeliverableCriticality.INFORMATIONAL,
        RequirementStatus.CONDITIONAL: DeliverableCriticality.CONDITIONAL,
        RequirementStatus.UNCLEAR: None,
    }[classification.requirement_status]


def needs_criticality(classification: ProposalClassification, condition_text: str | None) -> bool:
    """Whether the reviewer must choose how firmly the item is required before accepting it."""
    if classification.requirement_status is RequirementStatus.UNCLEAR:
        return True
    return (
        classification.requirement_status is RequirementStatus.CONDITIONAL
        and classification.lifecycle is not DiscoveryLifecycle.CONDITIONAL
        and not (condition_text or "").strip()
    )


def is_acceptable(
    classification: ProposalClassification,
    condition_text: str | None,
    criticality_chosen: bool = False,
) -> tuple[bool, str]:
    """Whether a reviewer has supplied every deterministic choice needed to accept."""
    if classification.lifecycle is DiscoveryLifecycle.NOT_APPLICABLE:
        return False, "Confirmed not applicable items are rejected, not accepted"
    if classification.lifecycle is DiscoveryLifecycle.NEEDS_REVIEW:
        return False, "Move it to a lifecycle category first"
    if (
        classification.lifecycle is DiscoveryLifecycle.CONDITIONAL
        and classification.applies_at is None
    ):
        return False, "Choose whether the conditional item applies with the bid or after award"
    if (
        classification.lifecycle is DiscoveryLifecycle.CONDITIONAL
        and not (condition_text or "").strip()
    ):
        return False, "Add the stated condition before accepting"
    if needs_criticality(classification, condition_text) and not criticality_chosen:
        return False, "Choose how firmly the item is required"
    if destination_for(classification) is None:
        return False, "Move it to a lifecycle category first"
    return True, ""


def _contains(passage: str, fragment: object) -> bool:
    if not isinstance(fragment, str) or not fragment.strip():
        return False
    return " ".join(fragment.split()).casefold() in " ".join(passage.split()).casefold()


def _topic_from_category(category: object) -> str:
    if not isinstance(category, str):
        return "QUALIFICATIONS"
    candidate = "_".join(category.strip().upper().split())
    return candidate if candidate in _STANDARD_TOPIC_KEYS else "QUALIFICATIONS"


def guard_classification(
    raw: Mapping[str, Any],
    passage_text: str,
    *,
    classified_by: ClassifiedBy = ClassifiedBy.MODEL,
) -> ProposalClassification:
    """Apply deterministic G1-G6 guards to a model-produced candidate.

    ``timing_text`` is intentionally not part of :class:`ProposalClassification`; G2 is applied
    by consumers when they copy that candidate field into date evidence.  This function performs
    the same occurrence check while constructing all classification fields it owns and never
    mutates the supplied mapping.
    """
    model_lifecycle = DiscoveryLifecycle(raw["lifecycle"])
    lifecycle = model_lifecycle
    confidence = Confidence(raw["confidence"])
    review_reason = raw.get("review_reason")
    if isinstance(review_reason, str):
        review_reason = review_reason.strip() or None
    else:
        review_reason = None

    if locate_quote(passage_text, raw.get("source_quote")) is None:
        lifecycle = DiscoveryLifecycle.NEEDS_REVIEW
        confidence = Confidence.LOW
        review_reason = "The supporting sentence was not found in the source text"

    # Evaluate G2 here so callers use the same deterministic predicate.  The guarded timing
    # value remains candidate/date evidence and is therefore not a classification field.
    _verified_timing_text = (
        raw.get("timing_text") if _contains(passage_text, raw.get("timing_text")) else None
    )
    del _verified_timing_text

    responsible_party = raw.get("responsible_party")
    if not _contains(passage_text, responsible_party):
        responsible_party = None

    applies_at: AppliesAt | None = None
    if model_lifecycle is DiscoveryLifecycle.CONDITIONAL and raw.get("applies_at") is not None:
        applies_at = AppliesAt(raw["applies_at"])
    if (
        model_lifecycle is DiscoveryLifecycle.CONDITIONAL
        and not str(raw.get("condition_text") or "").strip()
    ):
        lifecycle = DiscoveryLifecycle.NEEDS_REVIEW
        review_reason = review_reason or "Conditional requirement has no condition text"

    if confidence is Confidence.LOW:
        lifecycle = DiscoveryLifecycle.NEEDS_REVIEW
        review_reason = review_reason or "Low-confidence classification requires review"

    item_kind = ItemKind(raw["item_kind"])
    commercial_topic = (
        _topic_from_category(raw.get("category"))
        if item_kind is ItemKind.COMMERCIAL_QUALIFICATION
        else None
    )
    return ProposalClassification(
        lifecycle=lifecycle,
        model_lifecycle=model_lifecycle,
        requirement_status=RequirementStatus(raw["requirement_status"]),
        item_kind=item_kind,
        description=str(raw.get("description") or ""),
        applies_at=applies_at if lifecycle is DiscoveryLifecycle.CONDITIONAL else None,
        commercial_topic=commercial_topic,
        responsible_party=(str(responsible_party) if responsible_party is not None else None),
        dependencies=(str(raw["dependencies"]) if raw.get("dependencies") is not None else None),
        confidence=confidence,
        review_reason=str(review_reason) if review_reason is not None else None,
        source_section=(
            str(raw["source_section"]) if raw.get("source_section") is not None else None
        ),
        classified_by=classified_by,
    )
