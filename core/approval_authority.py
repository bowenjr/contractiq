"""Deterministic TASK-15 authority, package, route, and event models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.schemas import Provenance


class PolicyLifecycle(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"


class CaseLifecycle(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    WITHDRAWN = "WITHDRAWN"


class RouteState(StrEnum):
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    REVOKED = "REVOKED"


class EventDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    ABSTAINED = "ABSTAINED"


class DecisionType(StrEnum):
    REQUIREMENT_OR_SCOPE_EXCEPTION = "REQUIREMENT_OR_SCOPE_EXCEPTION"
    SUPPLIER_OR_DELIVERABLE_EXCEPTION = "SUPPLIER_OR_DELIVERABLE_EXCEPTION"
    COMMERCIAL_BASIS_EXCEPTION = "COMMERCIAL_BASIS_EXCEPTION"
    CONTRACT_POSITION = "CONTRACT_POSITION"
    RESIDUAL_CONTRACT_RISK = "RESIDUAL_CONTRACT_RISK"
    COMMERCIAL_SCENARIO = "COMMERCIAL_SCENARIO"
    PRICE_AND_MARGIN = "PRICE_AND_MARGIN"
    FINANCIAL_EXPOSURE = "FINANCIAL_EXPOSURE"


class SubjectType(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    SCOPE_ITEM = "SCOPE_ITEM"
    INTERFACE = "INTERFACE"
    SUPPLIER_REQUEST_ITEM = "SUPPLIER_REQUEST_ITEM"
    SUPPLIER_RESPONSE_VERSION = "SUPPLIER_RESPONSE_VERSION"
    DELIVERABLE_OBLIGATION = "DELIVERABLE_OBLIGATION"
    DELIVERABLE_SUBMISSION_VERSION = "DELIVERABLE_SUBMISSION_VERSION"
    COMMERCIAL_ITEM = "COMMERCIAL_ITEM"
    COMMERCIAL_ASSESSMENT_VERSION = "COMMERCIAL_ASSESSMENT_VERSION"
    CONTRACT_ISSUE = "CONTRACT_ISSUE"
    CONTRACT_RISK_ASSESSMENT_VERSION = "CONTRACT_RISK_ASSESSMENT_VERSION"
    CONTROLLED_DOCUMENT_VERSION = "CONTROLLED_DOCUMENT_VERSION"
    COMMERCIAL_SCENARIO_FAMILY = "COMMERCIAL_SCENARIO_FAMILY"
    COMMERCIAL_SCENARIO_VERSION = "COMMERCIAL_SCENARIO_VERSION"
    COMMERCIAL_SCENARIO_COMPARISON_VERSION = "COMMERCIAL_SCENARIO_COMPARISON_VERSION"


class StageMode(StrEnum):
    ALL_REQUIRED = "ALL_REQUIRED"
    ANY_ONE = "ANY_ONE"


class AuthorityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str = Field(default_factory=lambda: f"POL-{uuid4().hex}")
    name: str
    description: str
    scope: str
    version_number: int = Field(default=1, ge=1)
    effective_from: datetime
    effective_until: datetime | None = None
    lifecycle_state: PolicyLifecycle = PolicyLifecycle.DRAFT
    roles: tuple[str, ...]
    rules: tuple[dict[str, Any], ...]
    stages: tuple[dict[str, Any], ...]
    created_by: str
    created_at: datetime
    provenance: Provenance


class RoleAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignment_id: str = Field(default_factory=lambda: f"ASN-{uuid4().hex}")
    policy_id: str
    role_code: str
    actor_id: str = Field(min_length=1, max_length=120)
    effective_from: datetime
    effective_until: datetime | None = None
    assigned_by: str
    rationale: str
    created_at: datetime
    provenance: Provenance


class DecisionCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(default_factory=lambda: f"CASE-{uuid4().hex}")
    bid_id: str
    case_code: str
    decision_type: DecisionType
    title: str
    owner: str
    lifecycle_state: CaseLifecycle = CaseLifecycle.DRAFT
    materiality: str = "UNASSESSED"
    due_date: datetime | None = None
    version: int = 1
    created_by: str
    created_at: datetime
    provenance: Provenance


class SubjectLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    subject_link_id: str = Field(default_factory=lambda: f"SUB-{uuid4().hex}")
    bid_id: str
    case_id: str
    subject_type: SubjectType
    subject_id: str
    relation: str
    version_id: str | None = None
    created_at: datetime
    created_by: str


class DecisionPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_id: str = Field(default_factory=lambda: f"PKG-{uuid4().hex}")
    case_id: str
    bid_id: str
    version_number: int = Field(ge=1)
    issue: str
    options: tuple[str, ...]
    effects: dict[str, str]
    recommendation: str
    requested_outcome: str
    residual_risk: str
    deadline: datetime
    approval_valid_until: datetime | None = None
    subject_links: tuple[SubjectLink, ...]
    author: str
    created_at: datetime
    supersedes_package_id: str | None = None
    fingerprint: str | None = None

    @model_validator(mode="after")
    def complete(self) -> Self:
        if len(self.options) < 2:
            raise ValueError("active package requires at least two options")
        if not self.recommendation or not self.requested_outcome or not self.residual_risk:
            raise ValueError("package decision fields are required")
        canonical = "|".join(
            [
                self.case_id,
                str(self.version_number),
                self.issue,
                *self.options,
                self.recommendation,
                self.requested_outcome,
                self.residual_risk,
                *(f"{x.subject_type}:{x.subject_id}:{x.version_id}" for x in self.subject_links),
            ]
        )
        expected = sha256(canonical.encode()).hexdigest()
        if self.fingerprint is None:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("package fingerprint mismatch")
        return self


class RouteRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requirement_id: str = Field(default_factory=lambda: f"REQ-{uuid4().hex}")
    route_id: str
    stage_order: int
    stage_mode: StageMode
    role_code: str
    required: bool = True


class RouteCycle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route_id: str = Field(default_factory=lambda: f"ROUTE-{uuid4().hex}")
    case_id: str
    bid_id: str
    package_id: str
    policy_id: str
    state: RouteState = RouteState.PENDING
    matched_rule_ids: tuple[str, ...]
    requirements: tuple[RouteRequirement, ...]
    requestor: str
    submitted_at: datetime
    approval_valid_until: datetime | None = None
    version: int = 1


class ApprovalEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: str = Field(default_factory=lambda: f"EVT-{uuid4().hex}")
    route_id: str
    requirement_id: str
    package_id: str
    bid_id: str
    actor_id: str
    decision: EventDecision
    rationale: str | None = None
    created_at: datetime
