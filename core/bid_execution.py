"""Domain models for deterministic Bid execution overlays and notes."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.work_items import WorkItemPriority


class ExecutionSubjectKind(StrEnum):
    """Kinds of existing register records that can receive an execution overlay."""

    WORK_ITEM = "WORK_ITEM"
    RETURNABLE = "RETURNABLE"
    MANUFACTURER_INPUT = "MANUFACTURER_INPUT"

    @property
    def label(self) -> str:
        """Return wording suitable for business-facing screens."""
        return EXECUTION_SUBJECT_KIND_LABELS[self]

    @property
    def slug(self) -> str:
        """Return the stable URL segment for this subject kind."""
        return EXECUTION_SUBJECT_KIND_SLUGS[self]


class PrepStatus(StrEnum):
    """Business-facing preparation status stored only for returnables."""

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETE = "COMPLETE"
    NOT_REQUIRED = "NOT_REQUIRED"

    @property
    def label(self) -> str:
        """Return the business label without exposing the persisted enum value."""
        return PREP_STATUS_LABELS[self]


EXECUTION_SUBJECT_KIND_LABELS: dict[ExecutionSubjectKind, str] = {
    ExecutionSubjectKind.WORK_ITEM: "Pre-bid action",
    ExecutionSubjectKind.RETURNABLE: "Returnable",
    ExecutionSubjectKind.MANUFACTURER_INPUT: "Manufacturer input",
}

EXECUTION_SUBJECT_KIND_SLUGS: dict[ExecutionSubjectKind, str] = {
    ExecutionSubjectKind.WORK_ITEM: "work-item",
    ExecutionSubjectKind.RETURNABLE: "returnable",
    ExecutionSubjectKind.MANUFACTURER_INPUT: "manufacturer-input",
}

PREP_STATUS_LABELS: dict[PrepStatus, str] = {
    PrepStatus.NOT_STARTED: "Not started",
    PrepStatus.IN_PROGRESS: "In progress",
    PrepStatus.BLOCKED: "Blocked",
    PrepStatus.COMPLETE: "Complete",
    PrepStatus.NOT_REQUIRED: "Not required",
}


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class BidExecutionRecord(BaseModel):
    """Versioned, user-authored execution fields layered over an existing subject."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str
    bid_id: str
    subject_kind: ExecutionSubjectKind
    subject_id: str
    responsible: str | None = None
    contributors: list[str] = Field(default_factory=list)
    internal_due_date: date | None = None
    prep_status: PrepStatus | None = None
    priority: WorkItemPriority | None = None
    blocked_reason: str | None = None
    completion_evidence: str | None = None
    evidence_document_version_id: str | None = None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    updated_by: str

    @field_validator(
        "responsible",
        "blocked_reason",
        "completion_evidence",
        "evidence_document_version_id",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """Store blank optional text as absent."""
        return _optional_text(value)

    @field_validator("contributors")
    @classmethod
    def normalize_contributors(cls, values: list[str]) -> list[str]:
        """Trim, de-duplicate, and reject blank contributor labels."""
        normalized: list[str] = []
        for value in values:
            contributor = value.strip()
            if not contributor:
                raise ValueError("contributors cannot be blank; remove the blank entry")
            if contributor not in normalized:
                normalized.append(contributor)
        return normalized


class BidExecutionUpdate(BaseModel):
    """Complete desired overlay state guarded by an optional expected version."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int | None = Field(default=None, ge=0)
    responsible: str | None = None
    contributors: list[str] = Field(default_factory=list)
    internal_due_date: date | None = None
    prep_status: PrepStatus | None = None
    priority: WorkItemPriority | None = None
    blocked_reason: str | None = None
    completion_evidence: str | None = None
    evidence_document_version_id: str | None = None
    note: str | None = Field(default=None, max_length=4000)

    @field_validator(
        "responsible",
        "blocked_reason",
        "completion_evidence",
        "evidence_document_version_id",
        "note",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """Store blank optional text as absent."""
        return _optional_text(value)

    @field_validator("contributors")
    @classmethod
    def normalize_contributors(cls, values: list[str]) -> list[str]:
        """Apply the same contributor rules as persisted records."""
        return BidExecutionRecord.normalize_contributors(values)


class BidExecutionNote(BaseModel):
    """An immutable progress note attached to one execution subject."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    note_id: str
    bid_id: str
    subject_kind: ExecutionSubjectKind
    subject_id: str
    body: str = Field(min_length=1, max_length=4000)
    created_at: datetime
    created_by: str

    @field_validator("body")
    @classmethod
    def normalize_body(cls, value: str) -> str:
        """Trim the note and give an actionable refusal for blank text."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("note body cannot be blank; enter a progress note")
        return normalized

    @field_validator("created_by")
    @classmethod
    def normalize_created_by(cls, value: str) -> str:
        """Require the human or system actor that authored the note."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("note author cannot be blank; provide the current actor")
        return normalized

    def same_subject(self, record: BidExecutionRecord) -> bool:
        """Return whether this note belongs to the supplied overlay subject."""
        return (
            self.bid_id == record.bid_id
            and self.subject_kind is record.subject_kind
            and self.subject_id == record.subject_id
        )


class BidExecutionDependency(BaseModel):
    """An immutable dependency edge whose removal is recorded, never deleted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dependency_id: str
    bid_id: str
    item_kind: ExecutionSubjectKind
    item_id: str
    prerequisite_kind: ExecutionSubjectKind
    prerequisite_id: str
    note: str | None = Field(default=None, max_length=4000)
    created_at: datetime
    created_by: str
    removed_at: datetime | None = None
    removed_by: str | None = None

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        """Store a blank optional dependency note as absent."""
        return _optional_text(value)
