"""Typed domain models for the manual requirements compliance register."""

from datetime import date, datetime
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.schemas import Provenance

TITLE_MAX = 300
STATEMENT_MAX = 10_000
INTERPRETATION_MAX = 5_000
LOCATOR_MAX = 1_000
EXCERPT_MAX = 4_000
RESPONSE_MAX = 10_000
EVIDENCE_DESCRIPTION_MAX = 5_000
PROPOSAL_LOCATION_MAX = 1_000
ACTOR_LABEL_MAX = 200
REVIEW_NOTE_MAX = 5_000


class RequirementOrigin(str, Enum):  # noqa: UP042
    EXPLICIT = "EXPLICIT"
    IMPLIED = "IMPLIED"
    INTERNAL = "INTERNAL"


class RequirementCategory(str, Enum):  # noqa: UP042
    TECHNICAL = "TECHNICAL"
    SCOPE = "SCOPE"
    COMMERCIAL = "COMMERCIAL"
    CONTRACTUAL = "CONTRACTUAL"
    SCHEDULE = "SCHEDULE"
    QUALITY = "QUALITY"
    DOCUMENTATION = "DOCUMENTATION"
    SUBMISSION = "SUBMISSION"
    SUPPLIER = "SUPPLIER"
    REGULATORY = "REGULATORY"
    OTHER = "OTHER"


class RequirementSignificance(str, Enum):  # noqa: UP042
    DISQUALIFYING = "DISQUALIFYING"
    MANDATORY = "MANDATORY"
    SCORED = "SCORED"
    INFORMATIONAL = "INFORMATIONAL"


class RequirementStage(str, Enum):  # noqa: UP042
    BID = "BID"
    POST_AWARD = "POST_AWARD"
    BOTH = "BOTH"


class RequirementLifecycle(str, Enum):  # noqa: UP042
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"


class ResponseDisposition(str, Enum):  # noqa: UP042
    UNASSESSED = "UNASSESSED"
    COMPLY = "COMPLY"
    CLARIFY = "CLARIFY"
    DEVIATE = "DEVIATE"
    EXCLUDE = "EXCLUDE"
    OPTION = "OPTION"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RequirementWorkState(str, Enum):  # noqa: UP042
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    COMPLETE = "COMPLETE"


class RequirementReviewState(str, Enum):  # noqa: UP042
    NOT_REVIEWED = "NOT_REVIEWED"
    ACCEPTED = "ACCEPTED"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"


RESPONSE_REQUIRED_DISPOSITIONS = frozenset(
    {
        ResponseDisposition.COMPLY,
        ResponseDisposition.CLARIFY,
        ResponseDisposition.DEVIATE,
        ResponseDisposition.EXCLUDE,
        ResponseDisposition.OPTION,
    }
)
ATTENTION_SIGNIFICANCE = frozenset(
    {RequirementSignificance.DISQUALIFYING, RequirementSignificance.MANDATORY}
)
EXCEPTION_DISPOSITIONS = frozenset(
    {
        ResponseDisposition.CLARIFY,
        ResponseDisposition.DEVIATE,
        ResponseDisposition.EXCLUDE,
    }
)


def _trim_required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized


def _trim_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class RequirementCreate(BaseModel):
    """Validated request for one manually entered requirement."""

    model_config = ConfigDict(extra="forbid")

    bid_id: str
    title: str = Field(max_length=TITLE_MAX)
    statement: str = Field(max_length=STATEMENT_MAX)
    interpretation: str | None = Field(default=None, max_length=INTERPRETATION_MAX)
    origin: RequirementOrigin
    category: RequirementCategory
    significance: RequirementSignificance
    lifecycle_stage: RequirementStage = RequirementStage.BID
    owner: str | None = Field(default=None, max_length=ACTOR_LABEL_MAX)
    due_date: date | None = None
    source_document_version_id: str | None = None
    source_clause: str | None = Field(default=None, max_length=LOCATOR_MAX)
    source_page_start: int | None = Field(default=None, ge=1)
    source_page_end: int | None = Field(default=None, ge=1)
    source_locator_note: str | None = Field(default=None, max_length=LOCATOR_MAX)
    source_excerpt: str | None = Field(default=None, max_length=EXCERPT_MAX)

    @field_validator("bid_id", "title", "statement")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        return _trim_required(value, str(getattr(info, "field_name", "value")))

    @field_validator(
        "interpretation",
        "owner",
        "source_document_version_id",
        "source_clause",
        "source_locator_note",
        "source_excerpt",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _trim_optional(value)

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if self.interpretation == self.statement:
            raise ValueError("interpretation must be distinct from the requirement statement")
        if self.source_page_end is not None and self.source_page_start is None:
            raise ValueError("source_page_end requires source_page_start")
        if (
            self.source_page_start is not None
            and self.source_page_end is not None
            and self.source_page_end < self.source_page_start
        ):
            raise ValueError("source page range must be ordered")
        locators = (
            self.source_clause,
            self.source_page_start,
            self.source_locator_note,
            self.source_excerpt,
        )
        if self.origin == RequirementOrigin.EXPLICIT:
            if self.source_document_version_id is None:
                raise ValueError("EXPLICIT requirements require a controlled source version")
            if not any(value is not None for value in locators):
                raise ValueError("EXPLICIT requirements require a source locator")
        if self.source_document_version_id is None and any(value is not None for value in locators):
            raise ValueError("source locator fields require a controlled source version")
        return self


class RequirementMetadataEdit(BaseModel):
    """Permitted descriptive, ownership, and due-date edits."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=TITLE_MAX)
    statement: str | None = Field(default=None, max_length=STATEMENT_MAX)
    interpretation: str | None = Field(default=None, max_length=INTERPRETATION_MAX)
    category: RequirementCategory | None = None
    significance: RequirementSignificance | None = None
    lifecycle_stage: RequirementStage | None = None
    owner: str | None = Field(default=None, max_length=ACTOR_LABEL_MAX)
    due_date: date | None = None

    @field_validator("title", "statement")
    @classmethod
    def validate_required_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _trim_required(value, str(getattr(info, "field_name", "value")))

    @field_validator("interpretation", "owner")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _trim_optional(value)

    @model_validator(mode="after")
    def require_edit(self) -> Self:
        if self.model_fields_set == {"expected_version"}:
            raise ValueError("at least one metadata field is required")
        for field_name in ("title", "statement", "category", "significance", "lifecycle_stage"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class RequirementWorkflowUpdate(BaseModel):
    """Complete response/work snapshot guarded by optimistic concurrency."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    disposition: ResponseDisposition
    response_text: str | None = Field(default=None, max_length=RESPONSE_MAX)
    evidence_description: str | None = Field(default=None, max_length=EVIDENCE_DESCRIPTION_MAX)
    proposal_location: str | None = Field(default=None, max_length=PROPOSAL_LOCATION_MAX)
    work_state: RequirementWorkState

    @field_validator("response_text", "evidence_description", "proposal_location")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _trim_optional(value)

    @model_validator(mode="after")
    def validate_workflow(self) -> Self:
        _validate_workflow(self.disposition, self.response_text, self.work_state)
        return self


class RequirementReviewDecision(BaseModel):
    """Independent review decision with deterministic eligibility rules."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    review_state: RequirementReviewState
    reviewer: str = Field(max_length=ACTOR_LABEL_MAX)
    review_note: str | None = Field(default=None, max_length=REVIEW_NOTE_MAX)

    @field_validator("reviewer")
    @classmethod
    def validate_reviewer(cls, value: str) -> str:
        return _trim_required(value, "reviewer")

    @field_validator("review_note")
    @classmethod
    def normalize_review_note(cls, value: str | None) -> str | None:
        return _trim_optional(value)

    @model_validator(mode="after")
    def require_decision(self) -> Self:
        if self.review_state == RequirementReviewState.NOT_REVIEWED:
            raise ValueError("review decision must be ACCEPTED or CHANGES_REQUIRED")
        return self


class RequirementWithdraw(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class Requirement(BaseModel):
    """Authoritative bid-owned requirement and immutable primary source reference."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(pattern=r"^REQ-[0-9a-f-]{36}$")
    bid_id: str
    title: str = Field(max_length=TITLE_MAX)
    statement: str = Field(max_length=STATEMENT_MAX)
    interpretation: str | None = Field(default=None, max_length=INTERPRETATION_MAX)
    origin: RequirementOrigin
    category: RequirementCategory
    significance: RequirementSignificance
    lifecycle_stage: RequirementStage
    lifecycle_state: RequirementLifecycle
    superseded_by_requirement_id: str | None = None
    owner: str | None = Field(default=None, max_length=ACTOR_LABEL_MAX)
    due_date: date | None = None
    source_document_id: str | None = None
    source_document_version_id: str | None = None
    source_clause: str | None = Field(default=None, max_length=LOCATOR_MAX)
    source_page_start: int | None = Field(default=None, ge=1)
    source_page_end: int | None = Field(default=None, ge=1)
    source_locator_note: str | None = Field(default=None, max_length=LOCATOR_MAX)
    source_excerpt: str | None = Field(default=None, max_length=EXCERPT_MAX)
    disposition: ResponseDisposition
    response_text: str | None = Field(default=None, max_length=RESPONSE_MAX)
    evidence_description: str | None = Field(default=None, max_length=EVIDENCE_DESCRIPTION_MAX)
    proposal_location: str | None = Field(default=None, max_length=PROPOSAL_LOCATION_MAX)
    work_state: RequirementWorkState
    review_state: RequirementReviewState
    reviewer: str | None = Field(default=None, max_length=ACTOR_LABEL_MAX)
    review_note: str | None = Field(default=None, max_length=REVIEW_NOTE_MAX)
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)
    provenance: Provenance

    @field_validator("bid_id", "title", "statement")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        return _trim_required(value, str(getattr(info, "field_name", "value")))

    @field_validator(
        "interpretation",
        "owner",
        "source_document_id",
        "source_document_version_id",
        "source_clause",
        "source_locator_note",
        "source_excerpt",
        "response_text",
        "evidence_description",
        "proposal_location",
        "reviewer",
        "review_note",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _trim_optional(value)

    @model_validator(mode="after")
    def validate_invariants(self) -> Self:
        if self.interpretation == self.statement:
            raise ValueError("interpretation must be distinct from the requirement statement")
        if (self.source_document_id is None) != (self.source_document_version_id is None):
            raise ValueError("source document and version identity must be present together")
        if self.origin == RequirementOrigin.EXPLICIT and self.source_document_version_id is None:
            raise ValueError("EXPLICIT requirements require source evidence")
        if self.origin == RequirementOrigin.EXPLICIT and not any(
            value is not None
            for value in (
                self.source_clause,
                self.source_page_start,
                self.source_locator_note,
                self.source_excerpt,
            )
        ):
            raise ValueError("EXPLICIT requirements require a source locator")
        if self.source_page_end is not None and self.source_page_start is None:
            raise ValueError("source_page_end requires source_page_start")
        if (
            self.source_page_start is not None
            and self.source_page_end is not None
            and self.source_page_end < self.source_page_start
        ):
            raise ValueError("source page range must be ordered")
        if self.lifecycle_state == RequirementLifecycle.SUPERSEDED:
            if self.superseded_by_requirement_id is None:
                raise ValueError("SUPERSEDED requires a successor requirement")
        elif self.superseded_by_requirement_id is not None:
            raise ValueError("successor identity is reserved for SUPERSEDED requirements")
        _validate_workflow(self.disposition, self.response_text, self.work_state)
        if self.review_state == RequirementReviewState.ACCEPTED:
            if self.disposition == ResponseDisposition.UNASSESSED:
                raise ValueError("ACCEPTED review requires an assessed disposition")
            if self.work_state not in {
                RequirementWorkState.READY_FOR_REVIEW,
                RequirementWorkState.COMPLETE,
            }:
                raise ValueError("ACCEPTED review requires review-ready work")
            if self.reviewer is None:
                raise ValueError("ACCEPTED review requires a named reviewer")
        if self.review_state == RequirementReviewState.CHANGES_REQUIRED:
            if self.work_state == RequirementWorkState.COMPLETE:
                raise ValueError("CHANGES_REQUIRED cannot leave work COMPLETE")
            if self.reviewer is None:
                raise ValueError("CHANGES_REQUIRED requires a named reviewer")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("requirement timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self

    @property
    def fully_closed(self) -> bool:
        return (
            self.work_state == RequirementWorkState.COMPLETE
            and self.review_state == RequirementReviewState.ACCEPTED
        )

    @property
    def is_exception(self) -> bool:
        return self.disposition in EXCEPTION_DISPOSITIONS


class RequirementSourceCandidate(BaseModel):
    """Safe source-selection projection with no managed key or file contents."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bid_id: str
    document_id: str
    document_title: str
    document_lifecycle: str
    document_version_id: str
    version_label: str
    version_state: str
    sha256_abbreviation: str


class RequirementSourceChoices(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: list[RequirementSourceCandidate]
    excluded_document_count: int = Field(ge=0)
    exclusion_message: str | None = None


class RequirementDetail(BaseModel):
    """Requirement plus safe current source context for detail rendering."""

    model_config = ConfigDict(extra="forbid")

    requirement: Requirement
    source: RequirementSourceCandidate | None = None
    source_context_message: str | None = None


def _validate_workflow(
    disposition: ResponseDisposition,
    response_text: str | None,
    work_state: RequirementWorkState,
) -> None:
    if work_state == RequirementWorkState.COMPLETE:
        if disposition == ResponseDisposition.UNASSESSED:
            raise ValueError("COMPLETE requires an assessed disposition")
    if (
        work_state in {RequirementWorkState.READY_FOR_REVIEW, RequirementWorkState.COMPLETE}
        and disposition in RESPONSE_REQUIRED_DISPOSITIONS
        and response_text is None
    ):
        raise ValueError(f"{disposition.value} requires response text before review")
    if disposition == ResponseDisposition.NOT_APPLICABLE and response_text is None:
        raise ValueError("NOT_APPLICABLE requires a response rationale")
