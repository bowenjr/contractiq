"""Frozen command and result models for Bid execution screen actions."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from core.bid_execution import BidExecutionRecord, ExecutionSubjectKind, PrepStatus
from core.work_items import WorkItemPriority


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _display_status(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip().casefold()
    for status in PrepStatus:
        if normalized in {status.value.casefold(), status.label.casefold()}:
            return status
    return value


class ActionResult(BaseModel):
    """Business-facing outcome shared by every execution command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str
    reused: bool = False
    changed: bool
    subject_kind: ExecutionSubjectKind
    subject_id: str | None
    next_item_url: str | None = None


class WorkItemActionChanges(BaseModel):
    """Partial edits accepted from the pre-bid action screen."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: PrepStatus | None = None
    priority: WorkItemPriority | None = None
    due_date: date | None = None
    responsible: str | None = None
    contributors: list[str] | None = None
    blocker_description: str | None = None
    resolution_owner: str | None = None
    completion_outcome: str | None = None
    completion_evidence: str | None = None
    cancellation_reason: str | None = None
    note: str | None = Field(default=None, max_length=4000)

    @field_validator("status", mode="before")
    @classmethod
    def accept_display_status(cls, value: Any) -> Any:
        """Accept the labels posted by the execution screen."""
        return _display_status(value)

    @field_validator(
        "responsible",
        "blocker_description",
        "resolution_owner",
        "completion_outcome",
        "completion_evidence",
        "cancellation_reason",
        "note",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """Make an explicit blank a clear operation for optional text fields."""
        return _optional_text(value)

    @field_validator("contributors")
    @classmethod
    def normalize_contributors(cls, value: list[str] | None) -> list[str] | None:
        """Apply execution-record contributor normalization when supplied."""
        if value is None:
            return None
        return BidExecutionRecord.normalize_contributors(value)


class ReturnableChanges(BaseModel):
    """Partial overlay edits accepted from a returnable screen."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    responsible: str | None = None
    contributors: list[str] | None = None
    internal_due_date: date | None = None
    prep_status: PrepStatus | None = Field(
        default=None,
        validation_alias=AliasChoices("prep_status", "status"),
    )
    priority: WorkItemPriority | None = None
    blocked_reason: str | None = None
    completion_evidence: str | None = None
    evidence_document_version_id: str | None = None
    note: str | None = Field(default=None, max_length=4000)

    @field_validator("prep_status", mode="before")
    @classmethod
    def accept_display_status(cls, value: Any) -> Any:
        """Accept either a PrepStatus or its business display label."""
        return _display_status(value)

    @field_validator(
        "responsible",
        "blocked_reason",
        "completion_evidence",
        "evidence_document_version_id",
        "note",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """Preserve explicit clear semantics while normalizing blank text."""
        return _optional_text(value)

    @field_validator("contributors")
    @classmethod
    def normalize_contributors(cls, value: list[str] | None) -> list[str] | None:
        """Normalize a supplied contributor list without treating omission as clear."""
        if value is None:
            return None
        return BidExecutionRecord.normalize_contributors(value)


class ManufacturerInputChanges(BaseModel):
    """Partial overlay edits for a manufacturer input, whose status is read-only."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    responsible: str | None = None
    contributors: list[str] | None = None
    internal_due_date: date | None = None
    priority: WorkItemPriority | None = None
    note: str | None = Field(default=None, max_length=4000)
    status: PrepStatus | None = Field(
        default=None,
        validation_alias=AliasChoices("status", "prep_status"),
    )

    @field_validator("status", mode="before")
    @classmethod
    def accept_display_status(cls, value: Any) -> Any:
        """Parse status only so the service can return a screen-specific refusal."""
        return _display_status(value)

    @field_validator("responsible", "note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        """Normalize optional text while retaining fields-set information."""
        return _optional_text(value)

    @field_validator("contributors")
    @classmethod
    def normalize_contributors(cls, value: list[str] | None) -> list[str] | None:
        """Normalize a supplied contributor list without treating omission as clear."""
        if value is None:
            return None
        return BidExecutionRecord.normalize_contributors(value)


# Concise compatibility names for callers that describe these as update commands.
ActionChanges = WorkItemActionChanges
UpdateActionChanges = WorkItemActionChanges
UpdateReturnableChanges = ReturnableChanges
UpdateManufacturerInputChanges = ManufacturerInputChanges
