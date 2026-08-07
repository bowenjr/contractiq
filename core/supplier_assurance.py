"""Authoritative TASK-11 supplier assurance domain and pure projections."""

# Persisted wire-level enum strings intentionally follow repository conventions.
# ruff: noqa: E501, E701, E702, UP042
from datetime import date, datetime
from enum import Enum
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.schemas import Provenance


class Lifecycle(str, Enum):
    ACTIVE = "ACTIVE"
    WITHDRAWN = "WITHDRAWN"


class RequestType(str, Enum):
    REQUEST_FOR_QUOTE = "REQUEST_FOR_QUOTE"
    TECHNICAL_COMPLIANCE = "TECHNICAL_COMPLIANCE"
    COMMERCIAL_COMPLIANCE = "COMMERCIAL_COMPLIANCE"
    CLARIFICATION = "CLARIFICATION"
    DOCUMENT_DATA_REQUEST = "DOCUMENT_DATA_REQUEST"


class RequestState(str, Enum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    CLOSED = "CLOSED"


class Topic(str, Enum):
    SCOPE_SUPPLY = "SCOPE_SUPPLY"
    TECHNICAL_COMPLIANCE = "TECHNICAL_COMPLIANCE"
    LEAD_TIME = "LEAD_TIME"
    QUOTE_VALIDITY = "QUOTE_VALIDITY"
    WARRANTY_TERMS = "WARRANTY_TERMS"
    WARRANTY_START = "WARRANTY_START"
    TESTING_INSPECTION = "TESTING_INSPECTION"
    VENDOR_DATA_DOCUMENTATION = "VENDOR_DATA_DOCUMENTATION"
    CANCELLATION_RESCHEDULING = "CANCELLATION_RESCHEDULING"
    PRICE_ESCALATION = "PRICE_ESCALATION"
    FREIGHT_LOGISTICS = "FREIGHT_LOGISTICS"
    COUNTRY_OF_ORIGIN = "COUNTRY_OF_ORIGIN"
    CAPACITY_AVAILABILITY = "CAPACITY_AVAILABILITY"
    APPROVAL_AUTHORITY = "APPROVAL_AUTHORITY"
    LIABILITY_ALIGNMENT = "LIABILITY_ALIGNMENT"
    OTHER = "OTHER"


class SupportRole(str, Enum):
    REQUIRED_SUPPORT = "REQUIRED_SUPPORT"
    CANDIDATE_ALTERNATIVE = "CANDIDATE_ALTERNATIVE"
    INFORMATION_ONLY = "INFORMATION_ONLY"


class RequestMateriality(str, Enum):
    MATERIAL = "MATERIAL"
    NON_MATERIAL = "NON_MATERIAL"
    UNASSESSED = "UNASSESSED"


class FlowDownTargetType(str, Enum):
    REQUIREMENT = "REQUIREMENT"
    SCOPE_ITEM = "SCOPE_ITEM"
    INTERFACE = "INTERFACE"


class EvidenceMode(str, Enum):
    MANUAL_RECORD = "MANUAL_RECORD"
    CONTROLLED_DOCUMENT_VERSION = "CONTROLLED_DOCUMENT_VERSION"


class ValidityState(str, Enum):
    DATE_PROVIDED = "DATE_PROVIDED"
    NOT_PROVIDED = "NOT_PROVIDED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReviewState(str, Enum):
    NOT_REVIEWED = "NOT_REVIEWED"
    ACCEPTED = "ACCEPTED"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"


class CoverageState(str, Enum):
    CONFIRMED = "CONFIRMED"
    EXCEPTION = "EXCEPTION"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    SILENT = "SILENT"


class ExceptionKind(str, Enum):
    EXCLUSION = "EXCLUSION"
    DEVIATION = "DEVIATION"
    QUALIFICATION = "QUALIFICATION"
    ALTERNATIVE = "ALTERNATIVE"


def _text(value: str | None, label: str, max_len: int = 5000) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > max_len:
        raise ValueError(f"{label} exceeds maximum length")
    return value


class Supplier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_id: str = Field(default_factory=lambda: f"SUP-{uuid4().hex}")
    bid_id: str
    supplier_name: str = Field(min_length=1, max_length=300)
    manufacturer_name: str | None = None
    operator_reference: str | None = None
    note: str | None = None
    lifecycle_state: Lifecycle = Lifecycle.ACTIVE
    provenance: Provenance
    created_at: datetime
    updated_at: datetime
    version: int = Field(default=1, ge=1)
    created_by: str


class RequestItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_item_id: str = Field(default_factory=lambda: f"SRI-{uuid4().hex}")
    request_id: str
    bid_id: str
    sequence: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=300)
    confirmation_text: str = Field(min_length=1, max_length=5000)
    topic: Topic
    materiality: RequestMateriality = RequestMateriality.UNASSESSED
    support_role: SupportRole
    operator_note: str | None = None


class SupplierRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(default_factory=lambda: f"SREQ-{uuid4().hex}")
    bid_id: str
    supplier_id: str
    request_type: RequestType
    title: str = Field(min_length=1, max_length=300)
    external_reference: str | None = None
    purpose: str = Field(min_length=1, max_length=5000)
    owner: str = Field(min_length=1, max_length=200)
    due_date: date | None = None
    request_state: RequestState = RequestState.DRAFT
    issued_at: datetime | None = None
    closed_at: datetime | None = None
    close_rationale: str | None = None
    supersedes_request_id: str | None = None
    lifecycle_state: Lifecycle = Lifecycle.ACTIVE
    provenance: Provenance
    created_at: datetime
    updated_at: datetime
    version: int = Field(default=1, ge=1)
    created_by: str


class FlowDownLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    link_id: str = Field(default_factory=lambda: f"SFL-{uuid4().hex}")
    request_item_id: str
    bid_id: str
    target_type: FlowDownTargetType
    target_id: str = Field(min_length=1, max_length=200)
    created_at: datetime
    created_by: str


class Coverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_item_id: str
    state: CoverageState
    exception_kind: ExceptionKind | None = None
    evidence_text: str | None = None
    operator_note: str | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.state == CoverageState.EXCEPTION and (
            self.exception_kind is None or not self.evidence_text
        ):
            raise ValueError("exception requires kind and text")
        if (
            self.state in (CoverageState.CONFIRMED, CoverageState.NOT_APPLICABLE)
            and not self.evidence_text
        ):
            raise ValueError("coverage evidence is required")
        if self.state != CoverageState.EXCEPTION and self.exception_kind is not None:
            raise ValueError("exception kind only applies to exceptions")
        if self.state == CoverageState.SILENT and self.exception_kind is not None:
            raise ValueError("silent coverage cannot have exception kind")
        return self


class ResponseVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_version_id: str = Field(default_factory=lambda: f"SRV-{uuid4().hex}")
    response_id: str
    request_id: str
    supplier_id: str
    bid_id: str
    version_number: int = Field(ge=1)
    supplier_reference: str | None = None
    received_at: datetime
    evidence_mode: EvidenceMode
    document_version_id: str | None = None
    evidence_note: str | None = None
    validity_state: ValidityState = ValidityState.NOT_PROVIDED
    valid_until: date | None = None
    overall_note: str | None = None
    review_state: ReviewState = ReviewState.NOT_REVIEWED
    reviewer: str | None = None
    review_note: str | None = None
    created_at: datetime
    created_by: str

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.evidence_mode == EvidenceMode.MANUAL_RECORD and not self.evidence_note:
            raise ValueError("manual evidence requires note")
        if (
            self.evidence_mode == EvidenceMode.CONTROLLED_DOCUMENT_VERSION
            and not self.document_version_id
        ):
            raise ValueError("controlled evidence requires version")
        if self.validity_state == ValidityState.DATE_PROVIDED and self.valid_until is None:
            raise ValueError("validity date required")
        if self.review_state == ReviewState.ACCEPTED and not self.reviewer:
            raise ValueError("accepted response requires reviewer")
        return self


class Gap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: str
    entity_id: str
    bid_id: str
    severity: str
    explanation: str
    request_item_id: str | None = None


def supplier_gaps(
    requests: list[SupplierRequest],
    items: list[RequestItem],
    responses: list[ResponseVersion],
    coverage: list[Coverage],
    *,
    as_of_date: date,
) -> list[Gap]:
    """Pure deterministic supplier-assurance gap projection."""
    gaps: list[Gap] = []
    for req in sorted(
        (x for x in requests if x.lifecycle_state == Lifecycle.ACTIVE),
        key=lambda x: (x.bid_id, x.request_id),
    ):
        current = [r for r in responses if r.request_id == req.request_id]
        if (
            req.request_state == RequestState.ISSUED
            and not current
            and req.due_date
            and req.due_date < as_of_date
        ):
            gaps.append(
                Gap(
                    code="SUPPLIER_REQUEST_OVERDUE_NO_RESPONSE",
                    entity_id=req.request_id,
                    bid_id=req.bid_id,
                    severity="BLOCKING_ATTENTION",
                    explanation="Issued supplier request is overdue with no response.",
                )
            )
        if req.request_state == RequestState.CLOSED and not current:
            gaps.append(
                Gap(
                    code="SUPPLIER_REQUEST_CLOSED_WITHOUT_RESPONSE",
                    entity_id=req.request_id,
                    bid_id=req.bid_id,
                    severity="ADVISORY",
                    explanation="Request was closed without a response.",
                )
            )
    for response in sorted(responses, key=lambda x: (x.bid_id, x.response_id, x.version_number)):
        if response.review_state != ReviewState.ACCEPTED:
            gaps.append(
                Gap(
                    code="LATEST_RESPONSE_UNREVIEWED",
                    entity_id=response.response_version_id,
                    bid_id=response.bid_id,
                    severity="BLOCKING_ATTENTION",
                    explanation="Supplier response version awaits independent review.",
                )
            )
        if (
            response.validity_state == ValidityState.DATE_PROVIDED
            and response.valid_until
            and response.valid_until < as_of_date
        ):
            gaps.append(
                Gap(
                    code="SUPPLIER_RESPONSE_EXPIRED",
                    entity_id=response.response_version_id,
                    bid_id=response.bid_id,
                    severity="BLOCKING_ATTENTION",
                    explanation="Supplier response validity has expired.",
                )
            )
    for row in coverage:
        if row.state == CoverageState.SILENT:
            gaps.append(
                Gap(
                    code="SUPPLIER_RESPONSE_ITEM_SILENT",
                    entity_id=row.request_item_id,
                    bid_id="",
                    severity="BLOCKING_ATTENTION",
                    explanation="Supplier did not explicitly answer this request item.",
                    request_item_id=row.request_item_id,
                )
            )
        if row.state == CoverageState.EXCEPTION:
            gaps.append(
                Gap(
                    code="SUPPLIER_RESPONSE_EXCEPTION",
                    entity_id=row.request_item_id,
                    bid_id="",
                    severity="ADVISORY",
                    explanation="Supplier response records an exception.",
                    request_item_id=row.request_item_id,
                )
            )
    return sorted(gaps, key=lambda g: (g.bid_id, g.entity_id, g.code))
