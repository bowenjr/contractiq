"""Typed domain models for controlled documents and immutable file evidence."""

from datetime import date, datetime
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.schemas import Provenance


class DocumentCategory(str, Enum):  # noqa: UP042 - persisted strings are intentional
    SOLICITATION = "SOLICITATION"
    ADDENDUM = "ADDENDUM"
    SPECIFICATION = "SPECIFICATION"
    DRAWING = "DRAWING"
    COMMERCIAL = "COMMERCIAL"
    CONTRACTUAL = "CONTRACTUAL"
    SUPPLIER = "SUPPLIER"
    INTERNAL = "INTERNAL"
    DELIVERABLE = "DELIVERABLE"
    OTHER = "OTHER"


class DocumentLifecycle(str, Enum):  # noqa: UP042
    ACTIVE = "ACTIVE"
    WITHDRAWN = "WITHDRAWN"


class DocumentVersionState(str, Enum):  # noqa: UP042
    CURRENT = "CURRENT"
    SUPERSEDED = "SUPERSEDED"


class IntegrityStatus(str, Enum):  # noqa: UP042
    OK = "OK"
    MISSING = "MISSING"
    UNREADABLE = "UNREADABLE"
    SIZE_MISMATCH = "SIZE_MISMATCH"
    HASH_MISMATCH = "HASH_MISMATCH"


class LogicalIntegrityStatus(str, Enum):  # noqa: UP042
    IDENTITY_CORRUPT = "IDENTITY_CORRUPT"
    CURRENT_COUNT_INVALID = "CURRENT_COUNT_INVALID"
    MISSING_CURRENT_POINTER = "MISSING_CURRENT_POINTER"
    POINTER_MISSING = "POINTER_MISSING"
    POINTER_CROSS_DOCUMENT = "POINTER_CROSS_DOCUMENT"
    POINTER_STATE_MISMATCH = "POINTER_STATE_MISMATCH"
    CURRENT_POINTER_MISMATCH = "CURRENT_POINTER_MISMATCH"
    LINEAGE_MISSING_PREDECESSOR = "LINEAGE_MISSING_PREDECESSOR"
    LINEAGE_CROSS_DOCUMENT = "LINEAGE_CROSS_DOCUMENT"
    LINEAGE_CYCLE = "LINEAGE_CYCLE"
    LINEAGE_DISCONNECTED = "LINEAGE_DISCONNECTED"


class ControlledDocumentIntegrityError(ValueError):
    """Raised when persisted controlled identity cannot be decoded safely."""


class ControlledDocumentIdentityError(ValueError):
    """Raised when a legacy API attempts to mutate controlled identity."""


def _required(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


def _optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


class DocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bid_id: str
    title: str = Field(max_length=300)
    document_number: str | None = Field(default=None, max_length=200)
    category: DocumentCategory
    issuer: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=10_000)
    version_label: str = Field(max_length=200)
    issued_date: date | None = None
    received_at: datetime | None = None

    @field_validator("bid_id", "title", "version_label")
    @classmethod
    def required_text(cls, value: str, info: object) -> str:
        return _required(value, str(getattr(info, "field_name", "value")))

    @field_validator("document_number", "issuer", "notes")
    @classmethod
    def optional_text(cls, value: str | None) -> str | None:
        return _optional(value)

    @field_validator("received_at")
    @classmethod
    def aware_received_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("received_at must be timezone-aware")
        return value


class DocumentMetadataEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=300)
    document_number: str | None = Field(default=None, max_length=200)
    category: DocumentCategory | None = None
    issuer: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=10_000)

    @field_validator("title")
    @classmethod
    def title_text(cls, value: str | None) -> str | None:
        return None if value is None else _required(value, "title")

    @field_validator("document_number", "issuer", "notes")
    @classmethod
    def optional_text(cls, value: str | None) -> str | None:
        return _optional(value)

    @model_validator(mode="after")
    def require_edit(self) -> Self:
        if self.model_fields_set == {"expected_version"}:
            raise ValueError("at least one editable field is required")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title cannot be null")
        if "category" in self.model_fields_set and self.category is None:
            raise ValueError("category cannot be null")
        return self


class DocumentVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_label: str = Field(max_length=200)
    issued_date: date | None = None
    received_at: datetime | None = None
    expected_document_version: int = Field(ge=1)
    expected_current_version_id: str

    @field_validator("version_label", "expected_current_version_id")
    @classmethod
    def required_text(cls, value: str, info: object) -> str:
        return _required(value, str(getattr(info, "field_name", "value")))

    @field_validator("received_at")
    @classmethod
    def aware_received_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("received_at must be timezone-aware")
        return value


class ControlledDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(pattern=r"^DOC-[0-9a-f-]{36}$")
    bid_id: str
    title: str
    document_number: str | None = None
    category: DocumentCategory
    issuer: str | None = None
    notes: str | None = None
    lifecycle_state: DocumentLifecycle
    current_version_id: str = Field(pattern=r"^DV-[0-9a-f-]{36}$")
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)
    provenance: Provenance


class DocumentVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_version_id: str = Field(pattern=r"^DV-[0-9a-f-]{36}$")
    document_id: str = Field(pattern=r"^DOC-[0-9a-f-]{36}$")
    version_label: str
    issued_date: date | None = None
    received_at: datetime | None = None
    original_filename: str
    media_type: str | None = None
    byte_size: int = Field(gt=0)
    sha256_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    storage_key: str
    predecessor_version_id: str | None = None
    version_state: DocumentVersionState
    created_at: datetime
    provenance: Provenance


class IntegrityResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_version_id: str
    status: IntegrityStatus
    expected_size: int
    actual_size: int | None = None
    expected_sha256: str
    actual_sha256: str | None = None
    reason: str


class LogicalIntegrityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    status: LogicalIntegrityStatus
    reason: str


class DocumentRegisterEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    title: str
    bid_id: str | None = None
    category: DocumentCategory | None = None
    lifecycle_state: DocumentLifecycle | None = None
    document: ControlledDocument | None = None
    current_version: DocumentVersion | None = None
    logical_issues: list[LogicalIntegrityIssue] = Field(default_factory=list)


class StorageDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    committed_file_results: list[IntegrityResult]
    unreferenced_storage_keys: list[str]
    symlink_storage_keys: list[str] = Field(default_factory=list)
    logical_issues: list[LogicalIntegrityIssue] = Field(default_factory=list)
