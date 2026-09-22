"""Typed contracts for Bid package intake, addenda, and controlled Bid Basis evidence."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReleaseType(StrEnum):
    INITIAL_PACKAGE = "INITIAL_PACKAGE"
    ADDENDUM = "ADDENDUM"
    CLARIFICATION = "CLARIFICATION"
    REVISED_PACKAGE = "REVISED_PACKAGE"
    OTHER = "OTHER"


class ReleaseChannel(StrEnum):
    EMAIL = "EMAIL"
    PORTAL = "PORTAL"
    LETTER = "LETTER"
    TELEPHONE = "TELEPHONE"
    MEETING = "MEETING"
    ADDENDUM_DOCUMENT = "ADDENDUM_DOCUMENT"
    HAND_DELIVERY = "HAND_DELIVERY"
    OTHER = "OTHER"


class NoticeExpectation(StrEnum):
    EXPECTED = "EXPECTED"
    INFORMATION_ONLY = "INFORMATION_ONLY"
    RESOLVED_NOT_REQUIRED = "RESOLVED_NOT_REQUIRED"


class ContentForm(StrEnum):
    TEXTUAL = "TEXTUAL"
    DRAWING = "DRAWING"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class ClassificationMethod(StrEnum):
    SAFE_DEFAULT = "SAFE_DEFAULT"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    LOCAL_RULE = "LOCAL_RULE"
    LOCAL_AI_PROPOSAL = "LOCAL_AI_PROPOSAL"
    IMPORTED_AI_PROPOSAL = "IMPORTED_AI_PROPOSAL"


class AnalysisEligibility(StrEnum):
    NOT_ASSESSED = "NOT_ASSESSED"
    ELIGIBLE = "ELIGIBLE"
    EXCLUDED = "EXCLUDED"


class DirectiveMateriality(StrEnum):
    MATERIAL = "MATERIAL"
    NON_MATERIAL = "NON_MATERIAL"


class DirectiveDispositionStatus(StrEnum):
    INCORPORATED = "INCORPORATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INTERNAL_WAIVER = "INTERNAL_WAIVER"
    UNRESOLVED = "UNRESOLVED"


class AcknowledgementEventType(StrEnum):
    REQUIRED = "REQUIRED"
    NOT_REQUIRED = "NOT_REQUIRED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    EXCEPTION_APPROVED = "EXCEPTION_APPROVED"


class FileDocumentRelationship(StrEnum):
    EXACT_BYTES = "EXACT_BYTES"
    REPRESENTS_VERSION = "REPRESENTS_VERSION"
    SUPPORTING_EVIDENCE = "SUPPORTING_EVIDENCE"


class PreviewFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    original_relative_path: str = Field(min_length=1, max_length=2_048)
    original_filename: str = Field(min_length=1, max_length=512)
    extension: str = Field(max_length=64)
    detected_media_type: str | None = Field(default=None, max_length=255)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReleasePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_root_alias: str = Field(min_length=1, max_length=100)
    source_folder: str | None = Field(default=None, max_length=255)
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: tuple[PreviewFile, ...]
    total_bytes: int = Field(ge=0)

    @property
    def file_count(self) -> int:
        return len(self.files)


class IntakeInboxFolder(BaseModel):
    """Safe, non-authoritative view of one selectable immediate inbox folder."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_root_alias: str = Field(min_length=1, max_length=100)
    folder_name: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    file_count: int = Field(ge=0)
    supported_file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    last_modified_at: datetime
    source_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    already_imported: bool = False
    warning: str | None = Field(default=None, max_length=500)


class IntakeInbox(BaseModel):
    """Read-only configured inbox projection for routine package selection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_root_alias: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=255)
    folders: tuple[IntakeInboxFolder, ...]
    direct_file_count: int = Field(ge=0)
    warning: str | None = Field(default=None, max_length=500)


class ReleaseRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bid_id: str
    release_type: ReleaseType
    exact_customer_reference: str | None = Field(default=None, max_length=500)
    customer_issue_date: date | None = None
    received_at: datetime
    received_channel: ReleaseChannel
    source_root_alias: str = Field(min_length=1, max_length=100)
    source_folder: str | None = Field(default=None, max_length=255)
    expected_source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=10_000)

    @field_validator("bid_id", "source_root_alias", "operation_id")
    @classmethod
    def required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must be non-empty")
        return normalized

    @field_validator("exact_customer_reference", "note", "source_folder")
    @classmethod
    def optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("source_folder")
    @classmethod
    def immediate_source_folder(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("intake folder must be a single configured inbox folder")
        return value

    @field_validator("received_at")
    @classmethod
    def aware_received_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("received_at must be timezone-aware")
        return value


class ReleaseNoticeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bid_id: str
    release_type: ReleaseType
    exact_customer_reference: str | None = Field(default=None, max_length=500)
    customer_issue_date: date | None = None
    expected_receipt_date: date | None = None
    channel: ReleaseChannel
    observed_at: datetime
    expectation: NoticeExpectation = NoticeExpectation.EXPECTED
    summary: str = Field(min_length=1, max_length=10_000)
    evidence_document_version_id: str | None = None
    evidence_reference: str | None = Field(default=None, max_length=2_000)
    supersedes_notice_id: str | None = None
    operation_id: str = Field(min_length=1, max_length=200)

    @field_validator("observed_at")
    @classmethod
    def aware_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def evidence_present(self) -> ReleaseNoticeCreate:
        if self.channel in {ReleaseChannel.TELEPHONE, ReleaseChannel.MEETING}:
            return self
        if self.evidence_document_version_id is None and not self.evidence_reference:
            raise ValueError("document or reference evidence is required for this notice channel")
        return self


class ChannelCheckCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bid_id: str
    channel: ReleaseChannel
    checked_at: datetime
    observed_customer_reference: str | None = Field(default=None, max_length=500)
    customer_issue_date: date | None = None
    result: str = Field(min_length=1, max_length=2_000)
    evidence_reference: str | None = Field(default=None, max_length=2_000)
    operation_id: str = Field(min_length=1, max_length=200)

    @field_validator("checked_at")
    @classmethod
    def aware_checked_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("checked_at must be timezone-aware")
        return value


class FileDispositionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_form: ContentForm
    classification_method: ClassificationMethod
    confidence: float | None = Field(default=None, ge=0, le=1)
    analysis_eligibility: AnalysisEligibility
    exclusion_reason: str | None = Field(default=None, max_length=2_000)
    duplicate_of_file_id: str | None = None
    supersedes_event_id: str | None = None
    operation_id: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_eligibility(self) -> FileDispositionCreate:
        if self.analysis_eligibility is AnalysisEligibility.EXCLUDED and not self.exclusion_reason:
            raise ValueError("excluded files require an exclusion reason")
        if self.analysis_eligibility is not AnalysisEligibility.EXCLUDED and self.exclusion_reason:
            raise ValueError("exclusion reason is only valid for excluded files")
        return self


class BulkFileReviewItem(BaseModel):
    """One selected received-file and its current disposition concurrency token."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file_id: str = Field(min_length=1, max_length=200)
    supersedes_event_id: str = Field(min_length=1, max_length=200)


class BulkFileDispositionCreate(BaseModel):
    """One human review decision applied atomically to selected files in one release."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    release_id: str = Field(min_length=1, max_length=200)
    items: tuple[BulkFileReviewItem, ...] = Field(min_length=1)
    analysis_eligibility: AnalysisEligibility
    content_form: ContentForm | None = None
    exclusion_reason: str | None = Field(default=None, max_length=2_000)
    operation_id: str = Field(min_length=1, max_length=200)

    @field_validator("release_id", "operation_id")
    @classmethod
    def required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must be non-empty")
        return normalized

    @field_validator("exclusion_reason")
    @classmethod
    def optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_bulk_review(self) -> BulkFileDispositionCreate:
        file_ids = [item.file_id for item in self.items]
        if len(file_ids) != len(set(file_ids)):
            raise ValueError("a received file can only be selected once")
        if self.analysis_eligibility is AnalysisEligibility.EXCLUDED and not self.exclusion_reason:
            raise ValueError("excluded files require an exclusion reason")
        if self.analysis_eligibility is not AnalysisEligibility.EXCLUDED and self.exclusion_reason:
            raise ValueError("exclusion reason is only valid for excluded files")
        return self


class FileDocumentLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_version_id: str
    relationship: FileDocumentRelationship = FileDocumentRelationship.EXACT_BYTES
    supersedes_link_id: str | None = None
    operation_id: str = Field(min_length=1, max_length=200)


class DirectiveCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directive_type: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=10_000)
    materiality: DirectiveMateriality
    source_file_id: str | None = None
    source_locator: str | None = Field(default=None, max_length=2_000)
    target_document_version_id: str | None = None
    supersedes_directive_id: str | None = None
    operation_id: str = Field(min_length=1, max_length=200)


class DirectiveDispositionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DirectiveDispositionStatus
    rationale: str = Field(min_length=1, max_length=10_000)
    resulting_document_version_id: str | None = None
    approval_id: str | None = None
    route_id: str | None = None
    supersedes_disposition_id: str | None = None
    operation_id: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_status(self) -> DirectiveDispositionCreate:
        if self.status is DirectiveDispositionStatus.INCORPORATED:
            if self.resulting_document_version_id is None:
                raise ValueError("incorporated directives require an exact controlled version")
            if self.approval_id is not None or self.route_id is not None:
                raise ValueError(
                    "approval references do not replace customer incorporation evidence"
                )
        if self.status is DirectiveDispositionStatus.INTERNAL_WAIVER:
            if (self.approval_id is None) == (self.route_id is None):
                raise ValueError("internal waiver requires exactly one approval or route")
            if self.resulting_document_version_id is not None:
                raise ValueError("internal waiver cannot claim document incorporation")
        return self


class AcknowledgementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: AcknowledgementEventType
    due_at: datetime | None = None
    acknowledgement_reference: str | None = Field(default=None, max_length=2_000)
    approval_id: str | None = None
    route_id: str | None = None
    note: str | None = Field(default=None, max_length=10_000)
    supersedes_event_id: str | None = None
    operation_id: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_event(self) -> AcknowledgementCreate:
        if self.due_at is not None and self.due_at.tzinfo is None:
            raise ValueError("due_at must be timezone-aware")
        if self.event_type is AcknowledgementEventType.ACKNOWLEDGED:
            if not self.acknowledgement_reference:
                raise ValueError("acknowledgement requires a reference")
        if self.event_type is AcknowledgementEventType.EXCEPTION_APPROVED:
            if (self.approval_id is None) == (self.route_id is None):
                raise ValueError("acknowledgement exception requires exactly one approval or route")
        elif self.approval_id is not None or self.route_id is not None:
            raise ValueError("approval references are only valid for an approved exception")
        return self


class SnapshotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_current_snapshot_id: str | None = None
    release_ids: tuple[str, ...] = Field(min_length=1)
    label: str = Field(min_length=1, max_length=500)
    note: str | None = Field(default=None, max_length=10_000)
    operation_id: str = Field(min_length=1, max_length=200)

    @field_validator("release_ids")
    @classmethod
    def unique_releases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("release_ids must be unique")
        return value


class IntakeAttention(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    destination: str
    record_id: str | None = None
    due_at: datetime | None = None
    blocking: bool


class PackageIntakeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bid_id: str
    notices: tuple[dict[str, object], ...]
    channel_checks: tuple[dict[str, object], ...]
    releases: tuple[dict[str, object], ...]
    current_snapshot: dict[str, object] | None
    attention: tuple[IntakeAttention, ...]
    content_form_counts: dict[str, int]
    duplicate_count: int
    excluded_count: int


__all__ = [
    "AcknowledgementCreate",
    "AcknowledgementEventType",
    "AnalysisEligibility",
    "BulkFileDispositionCreate",
    "BulkFileReviewItem",
    "ChannelCheckCreate",
    "ClassificationMethod",
    "ContentForm",
    "DirectiveCreate",
    "DirectiveDispositionCreate",
    "DirectiveDispositionStatus",
    "DirectiveMateriality",
    "FileDispositionCreate",
    "FileDocumentLinkCreate",
    "FileDocumentRelationship",
    "IntakeAttention",
    "IntakeInbox",
    "IntakeInboxFolder",
    "NoticeExpectation",
    "PackageIntakeSummary",
    "PreviewFile",
    "ReleaseChannel",
    "ReleaseNoticeCreate",
    "ReleasePreview",
    "ReleaseRegistration",
    "ReleaseType",
    "SnapshotCreate",
]
