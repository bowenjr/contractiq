"""Authoritative TASK-12 deliverable obligations and deterministic projections."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.schemas import Provenance


class DeliverableCriticality(StrEnum):
    MANDATORY = "MANDATORY"
    CONDITIONAL = "CONDITIONAL"
    INFORMATIONAL = "INFORMATIONAL"


class LifecyclePhase(StrEnum):
    WITH_BID = "WITH_BID"
    PRE_AWARD = "PRE_AWARD"
    POST_AWARD = "POST_AWARD"
    BOTH = "BOTH"


class DeliverableDirection(StrEnum):
    SUPPLIER_TO_COMPANY = "SUPPLIER_TO_COMPANY"
    SUPPLIER_TO_CUSTOMER_VIA_COMPANY = "SUPPLIER_TO_CUSTOMER_VIA_COMPANY"
    COMPANY_TO_CUSTOMER = "COMPANY_TO_CUSTOMER"
    CUSTOMER_TO_COMPANY = "CUSTOMER_TO_COMPANY"
    INTERNAL = "INTERNAL"


class DueBasis(StrEnum):
    FIXED_DATE = "FIXED_DATE"
    OFFSET_FROM_EVENT = "OFFSET_FROM_EVENT"
    WITH_BID = "WITH_BID"
    ON_REQUEST = "ON_REQUEST"
    BEFORE_AWARD = "BEFORE_AWARD"
    AFTER_AWARD = "AFTER_AWARD"
    UNSCHEDULED = "UNSCHEDULED"


class DeliverableState(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SATISFIED = "SATISFIED"
    CANCELLED = "CANCELLED"


class SubmissionDisposition(StrEnum):
    SUBMITTED = "SUBMITTED"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"


class ReviewDecision(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    REVISION_REQUIRED = "REVISION_REQUIRED"


class DeliverableTargetType(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    SCOPE_ITEM = "SCOPE_ITEM"
    INTERFACE = "INTERFACE"
    SUPPLIER_REQUEST_ITEM = "SUPPLIER_REQUEST_ITEM"
    SUPPLIER_RESPONSE_VERSION = "SUPPLIER_RESPONSE_VERSION"
    DOCUMENT_VERSION = "DOCUMENT_VERSION"


class DeliverableRelation(StrEnum):
    CREATED_BY_REQUIREMENT = "CREATED_BY_REQUIREMENT"
    EVIDENCES_REQUIREMENT = "EVIDENCES_REQUIREMENT"
    SUPPORTS_SCOPE_ITEM = "SUPPORTS_SCOPE_ITEM"
    RESOLVES_INTERFACE = "RESOLVES_INTERFACE"
    FLOWS_DOWN_TO_SUPPLIER = "FLOWS_DOWN_TO_SUPPLIER"
    SUPPORTED_BY_SUPPLIER_RESPONSE = "SUPPORTED_BY_SUPPLIER_RESPONSE"
    SOURCE_DOCUMENT_VERSION = "SOURCE_DOCUMENT_VERSION"


class EvidenceMode(StrEnum):
    MANUAL_RECORD = "MANUAL_RECORD"
    CONTROLLED_DOCUMENT_VERSION = "CONTROLLED_DOCUMENT_VERSION"


class Deliverable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deliverable_id: str = Field(default_factory=lambda: f"DEL-{uuid4().hex}")
    bid_id: str
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=5000)
    category: str = Field(min_length=1, max_length=100)
    criticality: DeliverableCriticality
    materiality: str = "UNASSESSED"
    lifecycle_phase: LifecyclePhase
    direction: DeliverableDirection
    workflow_state: DeliverableState = DeliverableState.DRAFT
    owner: str | None = None
    supplier_id: str | None = None
    recipient: str | None = None
    due_basis: DueBasis = DueBasis.UNSCHEDULED
    fixed_due_date: date | None = None
    event_name: str | None = None
    offset_days: int | None = None
    condition_text: str | None = None
    condition_active: bool = True
    required_format: str | None = None
    required_review_role: str | None = None
    cancel_reason: str | None = None
    version: int = Field(default=1, ge=1)
    provenance: Provenance
    created_at: datetime
    updated_at: datetime
    created_by: str

    @model_validator(mode="after")
    def semantic(self) -> Self:
        supplier_direction = self.direction in {
            DeliverableDirection.SUPPLIER_TO_COMPANY,
            DeliverableDirection.SUPPLIER_TO_CUSTOMER_VIA_COMPANY,
        }
        if supplier_direction and not self.supplier_id:
            raise ValueError("supplier direction requires supplier_id")
        if not supplier_direction and self.supplier_id:
            raise ValueError("non-supplier direction cannot carry supplier_id")
        if self.due_basis is DueBasis.FIXED_DATE and self.fixed_due_date is None:
            raise ValueError("fixed due basis requires fixed_due_date")
        if self.due_basis is DueBasis.OFFSET_FROM_EVENT and (
            not self.event_name or self.offset_days is None
        ):
            raise ValueError("offset due basis requires event and offset")
        if self.criticality is DeliverableCriticality.CONDITIONAL and not self.condition_text:
            raise ValueError("conditional deliverable requires condition_text")
        if self.workflow_state is DeliverableState.CANCELLED and not self.cancel_reason:
            raise ValueError("cancelled deliverable requires cancel_reason")
        return self


class DeliverableLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    link_id: str = Field(default_factory=lambda: f"DLK-{uuid4().hex}")
    bid_id: str
    deliverable_id: str
    target_type: DeliverableTargetType
    target_id: str
    relation: DeliverableRelation
    created_at: datetime
    created_by: str


class SupplierCommitment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commitment_id: str = Field(default_factory=lambda: f"COM-{uuid4().hex}")
    deliverable_id: str
    bid_id: str
    supplier_id: str
    response_version_id: str
    committed_due_date: date
    validity_until: date | None = None
    supersedes_commitment_id: str | None = None
    created_at: datetime
    created_by: str


class SubmissionVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    submission_id: str = Field(default_factory=lambda: f"SUB-{uuid4().hex}")
    deliverable_id: str
    bid_id: str
    version_number: int = Field(ge=1)
    sender: str
    recipient: str
    submitted_at: datetime
    evidence_mode: EvidenceMode
    document_version_id: str | None = None
    evidence_note: str | None = None
    reference: str | None = None
    expires_at: date | None = None
    disposition: SubmissionDisposition = SubmissionDisposition.SUBMITTED
    created_at: datetime
    created_by: str

    @model_validator(mode="after")
    def evidence(self) -> Self:
        if (
            self.evidence_mode is EvidenceMode.CONTROLLED_DOCUMENT_VERSION
            and not self.document_version_id
        ):
            raise ValueError("controlled evidence requires document_version_id")
        if self.evidence_mode is EvidenceMode.MANUAL_RECORD and not self.evidence_note:
            raise ValueError("manual evidence requires evidence_note")
        return self


class ReviewDecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str = Field(default_factory=lambda: f"DREV-{uuid4().hex}")
    deliverable_id: str
    bid_id: str
    submission_id: str
    decision: ReviewDecision
    reviewer: str = Field(min_length=1)
    rationale: str | None = None
    reviewed_at: datetime
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def rationale_required(self) -> Self:
        if self.decision is not ReviewDecision.ACCEPTED and not self.rationale:
            raise ValueError("rejection or revision requires rationale")
        return self


class DeliverableGap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: str
    bid_id: str
    deliverable_id: str
    severity: str
    explanation: str
    source_ids: tuple[str, ...] = ()
    dedup_key: str


def deliverable_gaps(
    items: list[Deliverable], *, as_of: date, due_soon_days: int = 7
) -> list[DeliverableGap]:
    gaps: list[DeliverableGap] = []
    for item in sorted(items, key=lambda x: (x.bid_id, x.deliverable_id)):
        if item.workflow_state in {DeliverableState.CANCELLED, DeliverableState.SATISFIED} or (
            item.criticality is DeliverableCriticality.CONDITIONAL and not item.condition_active
        ):
            continue
        required = item.criticality is DeliverableCriticality.MANDATORY or item.condition_active
        severity = (
            "BLOCKING_ATTENTION"
            if required and item.lifecycle_phase in {LifecyclePhase.WITH_BID, LifecyclePhase.BOTH}
            else "ADVISORY"
        )

        def add(
            code: str,
            explanation: str,
            ids: tuple[str, ...] = (),
            current_item: Deliverable = item,
            current_severity: str = severity,
        ) -> None:
            gaps.append(
                DeliverableGap(
                    code=code,
                    bid_id=current_item.bid_id,
                    deliverable_id=current_item.deliverable_id,
                    severity=current_severity,
                    explanation=explanation,
                    source_ids=ids,
                    dedup_key=f"{current_item.deliverable_id}:{code}",
                )
            )

        if required and not item.owner:
            add("DELIVERABLE_REQUIRED_NO_OWNER", "Required deliverable has no owner.")
        if required and not item.recipient:
            add("DELIVERABLE_REQUIRED_NO_RECIPIENT", "Required deliverable has no recipient.")
        if required and item.due_basis is DueBasis.UNSCHEDULED:
            add(
                "DELIVERABLE_REQUIRED_UNSCHEDULED",
                "Required deliverable has no usable schedule basis.",
            )
        if item.supplier_id and required:
            add(
                "DELIVERABLE_SUPPLIER_NO_COMMITMENT",
                "Supplier deliverable has no current commitment.",
            )
        if item.due_basis is DueBasis.FIXED_DATE and item.fixed_due_date:
            if item.fixed_due_date < as_of:
                add(
                    "DELIVERABLE_OVERDUE_NO_SUBMISSION",
                    "Deliverable is overdue without a satisfied submission.",
                )
            elif (item.fixed_due_date - as_of).days <= due_soon_days:
                add("DELIVERABLE_DUE_SOON", "Deliverable is due soon.")
        add("DELIVERABLE_EVIDENCE_MISSING", "No accepted submission evidence is recorded.")
    return sorted(gaps, key=lambda gap: (gap.bid_id, gap.deliverable_id, gap.code))
