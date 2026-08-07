"""Authoritative TASK-14 contract-risk domain and deterministic rating."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.schemas import Provenance


class RiskCategory(StrEnum):
    SCOPE_PRECEDENCE = "SCOPE_PRECEDENCE"
    PRICE_ESCALATION = "PRICE_ESCALATION"
    PAYMENT_CREDIT = "PAYMENT_CREDIT"
    DELIVERY_SCHEDULE = "DELIVERY_SCHEDULE"
    LIQUIDATED_DAMAGES = "LIQUIDATED_DAMAGES"
    WARRANTY = "WARRANTY"
    PERFORMANCE_FITNESS = "PERFORMANCE_FITNESS"
    INDEMNITY = "INDEMNITY"
    LIMITATION_OF_LIABILITY = "LIMITATION_OF_LIABILITY"
    TERMINATION_CANCELLATION = "TERMINATION_CANCELLATION"
    CHANGES_CLAIMS = "CHANGES_CLAIMS"
    INSPECTION_ACCEPTANCE = "INSPECTION_ACCEPTANCE"
    INSURANCE_BONDS = "INSURANCE_BONDS"
    COMPLIANCE_AUDIT = "COMPLIANCE_AUDIT"
    DISPUTE_GOVERNING_LAW = "DISPUTE_GOVERNING_LAW"


class IssueLifecycle(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    WITHDRAWN = "WITHDRAWN"


class SourceType(StrEnum):
    CONTROLLED_DOCUMENT_VERSION = "CONTROLLED_DOCUMENT_VERSION"
    BOUNDED_MANUAL_SOURCE = "BOUNDED_MANUAL_SOURCE"


class ProposedDisposition(StrEnum):
    UNRESOLVED = "UNRESOLVED"
    ALIGN_TO_COMPANY_POSITION = "ALIGN_TO_COMPANY_POSITION"
    CLARIFY = "CLARIFY"
    PROPOSE_DEVIATION = "PROPOSE_DEVIATION"
    PROPOSE_EXCLUSION = "PROPOSE_EXCLUSION"
    SEEK_EXCEPTION = "SEEK_EXCEPTION"
    ACCEPT_AS_WRITTEN_PROPOSED = "ACCEPT_AS_WRITTEN_PROPOSED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Likelihood(IntEnum):
    UNASSESSED = 0
    RARE = 1
    UNLIKELY = 2
    POSSIBLE = 3
    LIKELY = 4
    ALMOST_CERTAIN = 5


class Consequence(IntEnum):
    UNASSESSED = 0
    MINOR = 1
    MODERATE = 2
    MAJOR = 3
    SEVERE = 4
    CATASTROPHIC = 5


class RiskRating(StrEnum):
    UNASSESSED = "UNASSESSED"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ExposureBasis(StrEnum):
    NOT_ASSESSED = "NOT_ASSESSED"
    MONETARY_RANGE = "MONETARY_RANGE"
    PERCENT_OF_BID_VALUE = "PERCENT_OF_BID_VALUE"
    SCHEDULE_DAYS_RANGE = "SCHEDULE_DAYS_RANGE"
    QUALITATIVE_ONLY = "QUALITATIVE_ONLY"
    NOT_QUANTIFIABLE = "NOT_QUANTIFIABLE"
    UNLIMITED_OR_UNCAPPED = "UNLIMITED_OR_UNCAPPED"


class TargetType(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    SCOPE_ITEM = "SCOPE_ITEM"
    INTERFACE = "INTERFACE"
    SUPPLIER_REQUEST_ITEM = "SUPPLIER_REQUEST_ITEM"
    SUPPLIER_RESPONSE_VERSION = "SUPPLIER_RESPONSE_VERSION"
    DELIVERABLE_OBLIGATION = "DELIVERABLE_OBLIGATION"
    COMMERCIAL_ITEM = "COMMERCIAL_ITEM"
    CONTROLLED_DOCUMENT_VERSION = "CONTROLLED_DOCUMENT_VERSION"
    CONTRACT_ISSUE = "CONTRACT_ISSUE"


class Relation(StrEnum):
    SOURCE_CLAUSE = "SOURCE_CLAUSE"
    CREATES_REQUIREMENT_RESPONSE_RISK = "CREATES_REQUIREMENT_RESPONSE_RISK"
    AFFECTS_SCOPE_ITEM = "AFFECTS_SCOPE_ITEM"
    AFFECTS_INTERFACE = "AFFECTS_INTERFACE"
    AFFECTS_SUPPLIER_COMMITMENT = "AFFECTS_SUPPLIER_COMMITMENT"
    AFFECTS_DELIVERABLE = "AFFECTS_DELIVERABLE"
    AFFECTS_COMMERCIAL_ITEM = "AFFECTS_COMMERCIAL_ITEM"
    DUPLICATES_ISSUE = "DUPLICATES_ISSUE"
    SUPERSEDES_ISSUE = "SUPERSEDES_ISSUE"
    DEPENDS_ON_ISSUE = "DEPENDS_ON_ISSUE"
    RELATED_ISSUE = "RELATED_ISSUE"


class ReviewDecision(StrEnum):
    ACCEPTED = "ACCEPTED"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    REJECTED = "REJECTED"


def risk_rating(likelihood: Likelihood, consequence: Consequence) -> tuple[int | None, RiskRating]:
    if likelihood is Likelihood.UNASSESSED or consequence is Consequence.UNASSESSED:
        return None, RiskRating.UNASSESSED
    score = int(likelihood) * int(consequence)
    return (
        score,
        RiskRating.LOW
        if score <= 4
        else RiskRating.MEDIUM
        if score <= 9
        else RiskRating.HIGH
        if score <= 16
        else RiskRating.CRITICAL,
    )


class ContractIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str = Field(default_factory=lambda: f"CRI-{uuid4().hex}")
    bid_id: str
    issue_code: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=5000)
    owner: str | None = None
    materiality: str = "UNASSESSED"
    due_date: date | None = None
    lifecycle_state: IssueLifecycle = IssueLifecycle.DRAFT
    version: int = Field(default=1, ge=1)
    provenance: Provenance
    created_at: datetime
    updated_at: datetime
    created_by: str


class RiskSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str = Field(default_factory=lambda: f"CRS-{uuid4().hex}")
    bid_id: str
    issue_id: str
    source_type: SourceType
    target_id: str | None = None
    source_title: str | None = None
    issuer_role: str | None = None
    source_date: date | None = None
    locator: str = Field(min_length=1, max_length=500)
    rationale: str | None = None
    reviewed_at: date | None = None
    expires_at: date | None = None
    created_at: datetime
    created_by: str

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.source_type is SourceType.CONTROLLED_DOCUMENT_VERSION and not self.target_id:
            raise ValueError("controlled source requires target")
        if self.source_type is SourceType.BOUNDED_MANUAL_SOURCE and (
            not self.source_title or not self.issuer_role or not self.rationale
        ):
            raise ValueError("manual source requires bounded provenance")
        return self


class RiskLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    link_id: str = Field(default_factory=lambda: f"CRL-{uuid4().hex}")
    bid_id: str
    issue_id: str
    target_type: TargetType
    target_id: str
    relation: Relation
    created_at: datetime
    created_by: str


class RiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment_id: str = Field(default_factory=lambda: f"CRA-{uuid4().hex}")
    issue_id: str
    bid_id: str
    version_number: int = Field(ge=1)
    category: RiskCategory
    customer_position: str
    company_position: str | None = None
    target_position: str | None = None
    fallback_position: str | None = None
    business_impact: str
    affected_functions: tuple[str, ...] = ()
    disposition: ProposedDisposition = ProposedDisposition.UNRESOLVED
    likelihood: Likelihood = Likelihood.UNASSESSED
    consequence: Consequence = Consequence.UNASSESSED
    exposure_basis: ExposureBasis = ExposureBasis.NOT_ASSESSED
    minimum: Decimal | None = None
    most_likely: Decimal | None = None
    maximum: Decimal | None = None
    currency: str | None = None
    rationale: str | None = None
    escalation_owner: str | None = None
    assessed_by: str
    assessed_at: datetime
    supersedes_assessment_id: str | None = None
    provenance: Provenance
    created_at: datetime

    @field_validator("minimum", "most_likely", "maximum")
    @classmethod
    def nonnegative(cls, v: Decimal | None) -> Decimal | None:
        if v is not None and v < 0:
            raise ValueError("exposure must be non-negative")
        return v

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if not self.customer_position.strip() or not self.business_impact.strip():
            raise ValueError("customer position and impact are required")
        if self.disposition is not ProposedDisposition.NOT_APPLICABLE and not self.company_position:
            raise ValueError("company position is required")
        numeric = {
            ExposureBasis.MONETARY_RANGE,
            ExposureBasis.PERCENT_OF_BID_VALUE,
            ExposureBasis.SCHEDULE_DAYS_RANGE,
        }
        if self.exposure_basis is ExposureBasis.MONETARY_RANGE and (
            self.currency is None
            or any(v is None for v in (self.minimum, self.most_likely, self.maximum))
        ):
            raise ValueError("monetary range requires amount and currency")
        if self.exposure_basis in numeric and all(
            v is not None for v in (self.minimum, self.most_likely, self.maximum)
        ):
            assert (
                self.minimum is not None
                and self.most_likely is not None
                and self.maximum is not None
            )
            if not (self.minimum <= self.most_likely and self.most_likely <= self.maximum):
                raise ValueError("exposure range must be ordered")
        if self.exposure_basis is ExposureBasis.UNLIMITED_OR_UNCAPPED and not (
            self.rationale and self.escalation_owner
        ):
            raise ValueError("uncapped exposure requires rationale and escalation owner")
        return self


class RiskReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str = Field(default_factory=lambda: f"CRR-{uuid4().hex}")
    bid_id: str
    issue_id: str
    assessment_id: str
    decision: ReviewDecision
    reviewer: str
    rationale: str | None = None
    reviewed_at: datetime
    provenance: Provenance


class RiskGap(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: str
    bid_id: str
    issue_id: str | None = None
    severity: str
    explanation: str
    dedup_key: str
