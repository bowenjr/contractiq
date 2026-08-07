"""Authoritative TASK-13 commercial completeness domain models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.schemas import Provenance


class CommercialCategory(StrEnum):
    SCOPE_PRICE = "SCOPE_PRICE"
    FREIGHT_LOGISTICS = "FREIGHT_LOGISTICS"
    DUTY_BROKERAGE = "DUTY_BROKERAGE"
    TAXES_FEES = "TAXES_FEES"
    CURRENCY_FX = "CURRENCY_FX"
    PRICE_VALIDITY = "PRICE_VALIDITY"
    ESCALATION = "ESCALATION"
    PAYMENT_CARRY = "PAYMENT_CARRY"
    HOLDBACK_RETENTION = "HOLDBACK_RETENTION"
    BONDS_INSURANCE = "BONDS_INSURANCE"
    WARRANTY_SERVICE = "WARRANTY_SERVICE"
    TESTING_INSPECTION = "TESTING_INSPECTION"
    DOCUMENTATION = "DOCUMENTATION"
    FIELD_SERVICE = "FIELD_SERVICE"
    CONTINGENCY_RISK = "CONTINGENCY_RISK"
    OTHER = "OTHER"


class BasisRole(StrEnum):
    CUSTOMER_PRICE = "CUSTOMER_PRICE"
    SUPPLIER_COST = "SUPPLIER_COST"
    INTERNAL_COST = "INTERNAL_COST"
    COMMERCIAL_FACTOR = "COMMERCIAL_FACTOR"


class Applicability(StrEnum):
    UNASSESSED = "UNASSESSED"
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CommercialTreatment(StrEnum):
    UNRESOLVED = "UNRESOLVED"
    FIRM_PRICED = "FIRM_PRICED"
    SEPARATELY_PRICED = "SEPARATELY_PRICED"
    ALLOWANCED = "ALLOWANCED"
    NO_CHARGE = "NO_CHARGE"
    EXCLUDED = "EXCLUDED"
    CUSTOMER_RESPONSIBILITY = "CUSTOMER_RESPONSIBILITY"
    INCLUDED_ELSEWHERE = "INCLUDED_ELSEWHERE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class EvidenceBasis(StrEnum):
    CONTROLLED_DOCUMENT_VERSION = "CONTROLLED_DOCUMENT_VERSION"
    ACCEPTED_SUPPLIER_RESPONSE = "ACCEPTED_SUPPLIER_RESPONSE"
    INTERNAL_ESTIMATE = "INTERNAL_ESTIMATE"
    RATE_OR_SCHEDULE = "RATE_OR_SCHEDULE"
    BOUNDED_MANUAL_DECISION = "BOUNDED_MANUAL_DECISION"


class CommercialLifecycle(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    WITHDRAWN = "WITHDRAWN"


class ReviewDecision(StrEnum):
    ACCEPTED = "ACCEPTED"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    REJECTED = "REJECTED"


class CommercialTargetType(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    SCOPE_ITEM = "SCOPE_ITEM"
    INTERFACE = "INTERFACE"
    SUPPLIER_REQUEST_ITEM = "SUPPLIER_REQUEST_ITEM"
    SUPPLIER_RESPONSE_VERSION = "SUPPLIER_RESPONSE_VERSION"
    DELIVERABLE = "DELIVERABLE"
    DOCUMENT_VERSION = "DOCUMENT_VERSION"
    COMMERCIAL_ITEM = "COMMERCIAL_ITEM"


class CommercialRelation(StrEnum):
    PRICES_SCOPE_ITEM = "PRICES_SCOPE_ITEM"
    COST_SUPPORTS_SCOPE_ITEM = "COST_SUPPORTS_SCOPE_ITEM"
    ADDRESSES_REQUIREMENT = "ADDRESSES_REQUIREMENT"
    COVERS_INTERFACE = "COVERS_INTERFACE"
    SUPPORTED_BY_SUPPLIER_EVIDENCE = "SUPPORTED_BY_SUPPLIER_EVIDENCE"
    COVERS_DELIVERABLE_COST = "COVERS_DELIVERABLE_COST"
    SOURCE_DOCUMENT_VERSION = "SOURCE_DOCUMENT_VERSION"
    INCLUDED_IN_COMMERCIAL_ITEM = "INCLUDED_IN_COMMERCIAL_ITEM"


class CommercialItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commercial_item_id: str = Field(default_factory=lambda: f"COM-{uuid4().hex}")
    bid_id: str
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=5000)
    category: CommercialCategory
    basis_role: BasisRole
    materiality: str = "UNASSESSED"
    owner: str | None = None
    due_date: date | None = None
    lifecycle_state: CommercialLifecycle = CommercialLifecycle.DRAFT
    version: int = Field(default=1, ge=1)
    provenance: Provenance
    created_at: datetime
    updated_at: datetime
    created_by: str


class CommercialLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    link_id: str = Field(default_factory=lambda: f"CL-{uuid4().hex}")
    bid_id: str
    commercial_item_id: str
    target_type: CommercialTargetType
    target_id: str
    relation: CommercialRelation
    created_at: datetime
    created_by: str


class AssessmentVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment_id: str = Field(default_factory=lambda: f"CAS-{uuid4().hex}")
    commercial_item_id: str
    bid_id: str
    version_number: int = Field(ge=1)
    applicability: Applicability
    treatment: CommercialTreatment
    amount: Decimal | None = None
    currency: str | None = None
    evidence_basis: EvidenceBasis | None = None
    evidence_target_id: str | None = None
    rationale: str | None = None
    validity_until: date | None = None
    assessed_by: str
    assessed_at: datetime
    supersedes_assessment_id: str | None = None
    provenance: Provenance
    created_at: datetime

    @field_validator("amount")
    @classmethod
    def money_scale(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("amount must be non-negative")
        exponent = value.as_tuple().exponent if value is not None else 0
        if isinstance(exponent, int) and exponent < -6:
            raise ValueError("amount supports at most six decimal places")
        return value

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value: str | None) -> str | None:
        if value is not None and (len(value) != 3 or not value.isalpha() or value != value.upper()):
            raise ValueError("currency must be an uppercase three-letter code")
        return value

    @model_validator(mode="after")
    def coherent(self) -> Self:
        money = {
            CommercialTreatment.FIRM_PRICED,
            CommercialTreatment.SEPARATELY_PRICED,
            CommercialTreatment.ALLOWANCED,
        }
        if self.treatment in money and (self.amount is None or self.currency is None):
            raise ValueError("priced treatment requires amount and currency")
        if self.amount is not None and self.currency is None:
            raise ValueError("amount requires currency")
        if self.currency is not None and self.amount is None:
            raise ValueError("currency requires amount")
        if (
            self.treatment
            in {
                CommercialTreatment.NO_CHARGE,
                CommercialTreatment.EXCLUDED,
                CommercialTreatment.CUSTOMER_RESPONSIBILITY,
                CommercialTreatment.NOT_APPLICABLE,
            }
            and not self.rationale
        ):
            raise ValueError("this treatment requires rationale")
        if (
            self.applicability is Applicability.NOT_APPLICABLE
            and self.treatment is not CommercialTreatment.NOT_APPLICABLE
        ):
            raise ValueError("not applicable requires not applicable treatment")
        if (
            self.treatment is CommercialTreatment.NOT_APPLICABLE
            and self.applicability is not Applicability.NOT_APPLICABLE
        ):
            raise ValueError("not applicable treatment requires not applicable applicability")
        if self.treatment is CommercialTreatment.INCLUDED_ELSEWHERE and not self.evidence_target_id:
            raise ValueError("included elsewhere requires target item")
        return self


class CommercialReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str = Field(default_factory=lambda: f"CRV-{uuid4().hex}")
    bid_id: str
    commercial_item_id: str
    assessment_id: str
    decision: ReviewDecision
    reviewer: str
    rationale: str | None = None
    reviewed_at: datetime
    provenance: Provenance

    @model_validator(mode="after")
    def rationale_required(self) -> Self:
        if self.decision is not ReviewDecision.ACCEPTED and not self.rationale:
            raise ValueError("non-accepted review requires rationale")
        return self


class CommercialGap(BaseModel):
    model_config = ConfigDict(frozen=True)
    code: str
    bid_id: str
    commercial_item_id: str | None = None
    target_id: str | None = None
    severity: str
    explanation: str
    dedup_key: str
