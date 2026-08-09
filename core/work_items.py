"""Domain models and validation for ContractIQ operational work items."""
# ruff: noqa: UP042

from datetime import date, datetime
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.schemas import Provenance


class WorkItemKind(str, Enum):  # noqa: UP042 - persisted string enum by design
    TASK = "TASK"
    MILESTONE = "MILESTONE"


class WorkItemStatus(str, Enum):  # noqa: UP042 - persisted string enum by design
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class WorkItemPriority(str, Enum):  # noqa: UP042 - persisted string enum by design
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class WorkCategory(str, Enum):
    OPPORTUNITY_DEVELOPMENT = "OPPORTUNITY_DEVELOPMENT"
    SAM_SUPPORT = "SAM_SUPPORT"
    CUSTOMER_REQUEST = "CUSTOMER_REQUEST"
    CUSTOMER_MEETING = "CUSTOMER_MEETING"
    SUPPLIER_REQUEST = "SUPPLIER_REQUEST"
    PRODUCT_TECHNICAL = "PRODUCT_TECHNICAL"
    QUOTATION_PRICING = "QUOTATION_PRICING"
    COMMERCIAL_REVIEW = "COMMERCIAL_REVIEW"
    PROJECT_COORDINATION = "PROJECT_COORDINATION"
    OPERATIONAL_ISSUE = "OPERATIONAL_ISSUE"
    MANAGEMENT_REQUEST = "MANAGEMENT_REQUEST"
    REPORT_PRESENTATION = "REPORT_PRESENTATION"
    PROCESS_IMPROVEMENT = "PROCESS_IMPROVEMENT"
    KNOWLEDGE_RESEARCH = "KNOWLEDGE_RESEARCH"
    TRAINING_DEVELOPMENT = "TRAINING_DEVELOPMENT"
    STRATEGIC_INITIATIVE = "STRATEGIC_INITIATIVE"
    ADMINISTRATIVE = "ADMINISTRATIVE"
    OTHER = "OTHER"


class ResponsibilityDomain(str, Enum):
    STRATEGIC_OPPORTUNITY = "STRATEGIC_OPPORTUNITY"
    EPC_PROJECT_PURSUIT = "EPC_PROJECT_PURSUIT"
    SAM_ENABLEMENT = "SAM_ENABLEMENT"
    CUSTOMER_SOLUTION = "CUSTOMER_SOLUTION"
    SUPPLIER_COORDINATION = "SUPPLIER_COORDINATION"
    QUOTATION_COMMERCIAL = "QUOTATION_COMMERCIAL"
    CROSS_FUNCTIONAL = "CROSS_FUNCTIONAL"
    RISK_ASSURANCE = "RISK_ASSURANCE"
    ACCOUNT_INTELLIGENCE = "ACCOUNT_INTELLIGENCE"
    OPERATIONAL_SUPPORT = "OPERATIONAL_SUPPORT"
    MANAGEMENT_INTELLIGENCE = "MANAGEMENT_INTELLIGENCE"
    PROCESS_KNOWLEDGE = "PROCESS_KNOWLEDGE"
    ROLE_DEVELOPMENT = "ROLE_DEVELOPMENT"


class WaitingPartyKind(str, Enum):
    PERSON = "PERSON"
    INTERNAL_FUNCTION = "INTERNAL_FUNCTION"
    EXTERNAL_ORGANIZATION = "EXTERNAL_ORGANIZATION"
    CUSTOMER = "CUSTOMER"
    SUPPLIER_MANUFACTURER = "SUPPLIER_MANUFACTURER"
    OTHER = "OTHER"


ACTIVE_WORK_ITEM_STATUSES = frozenset(
    {
        WorkItemStatus.OPEN,
        WorkItemStatus.IN_PROGRESS,
        WorkItemStatus.WAITING,
        WorkItemStatus.BLOCKED,
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


class WorkItemCreate(BaseModel):
    """Validated request to create one operational work item."""

    model_config = ConfigDict(extra="forbid")

    bid_id: str | None = None
    kind: WorkItemKind = WorkItemKind.TASK
    title: str = Field(max_length=300)
    details: str | None = Field(default=None, max_length=10_000)
    status: WorkItemStatus = WorkItemStatus.OPEN
    priority: WorkItemPriority = WorkItemPriority.NORMAL
    due_date: date | None = None
    waiting_on: str | None = Field(default=None, max_length=1_000)
    blocker_note: str | None = Field(default=None, max_length=2_000)
    category: WorkCategory = WorkCategory.OTHER
    responsibility_domain: ResponsibilityDomain | None = None
    next_action_date: date | None = None
    requester_label: str | None = None
    waiting_party_kind: WaitingPartyKind | None = None
    waiting_party_label: str | None = None
    waiting_owed: str | None = None
    requested_date: date | None = None
    chase_date: date | None = None
    blocker_description: str | None = None
    resolution_owner: str | None = None
    review_date: date | None = None
    completion_outcome: str | None = None
    contribution_candidate: bool = False
    completion_evidence: str | None = None

    @field_validator("title")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = str(getattr(info, "field_name", "value"))
        return _trim_required(value, field_name)

    @field_validator("details", "waiting_on", "blocker_note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _trim_optional(value)

    @model_validator(mode="after")
    def validate_conditional_fields(self) -> Self:
        if self.kind == WorkItemKind.MILESTONE and self.due_date is None:
            raise ValueError("MILESTONE requires a due_date")
        if self.status == WorkItemStatus.WAITING and self.waiting_on is None:
            raise ValueError("WAITING requires waiting_on")
        if self.status == WorkItemStatus.BLOCKED and self.blocker_note is None:
            raise ValueError("BLOCKED requires blocker_note")
        return self


class WorkItemEdit(BaseModel):
    """Permitted descriptive edits guarded by an expected version."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    kind: WorkItemKind | None = None
    title: str | None = Field(default=None, max_length=300)
    details: str | None = Field(default=None, max_length=10_000)
    priority: WorkItemPriority | None = None
    due_date: date | None = None
    category: WorkCategory | None = None
    responsibility_domain: ResponsibilityDomain | None = None
    next_action_date: date | None = None
    requester_label: str | None = None
    contribution_candidate: bool | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _trim_required(value, "title")

    @field_validator("details")
    @classmethod
    def normalize_details(cls, value: str | None) -> str | None:
        return _trim_optional(value)

    @model_validator(mode="after")
    def require_an_edit(self) -> Self:
        if self.model_fields_set == {"expected_version"}:
            raise ValueError("at least one editable field is required")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title cannot be null")
        if "kind" in self.model_fields_set and self.kind is None:
            raise ValueError("kind cannot be null")
        if "priority" in self.model_fields_set and self.priority is None:
            raise ValueError("priority cannot be null")
        return self


class WorkItemTransition(BaseModel):
    """Explicit work-item state transition request."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    status: WorkItemStatus
    waiting_on: str | None = Field(default=None, max_length=1_000)
    blocker_note: str | None = Field(default=None, max_length=2_000)
    waiting_party_kind: WaitingPartyKind | None = None
    waiting_party_label: str | None = None
    waiting_owed: str | None = None
    requested_date: date | None = None
    chase_date: date | None = None
    blocker_description: str | None = None
    resolution_owner: str | None = None
    review_date: date | None = None
    completion_outcome: str | None = None
    completion_evidence: str | None = None
    contribution_candidate: bool | None = None

    @field_validator("waiting_on", "blocker_note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _trim_optional(value)

    @model_validator(mode="after")
    def validate_conditional_fields(self) -> Self:
        if self.status == WorkItemStatus.WAITING and self.waiting_on is None:
            raise ValueError("WAITING requires waiting_on")
        if self.status == WorkItemStatus.BLOCKED and self.blocker_note is None:
            raise ValueError("BLOCKED requires blocker_note")
        return self


class WorkItem(BaseModel):
    """Authoritative snapshot of one auditable operational work item."""

    model_config = ConfigDict(extra="forbid")

    work_item_id: str = Field(pattern=r"^WI-[0-9a-f-]{36}$")
    bid_id: str | None = None
    kind: WorkItemKind
    title: str = Field(max_length=300)
    details: str | None = Field(default=None, max_length=10_000)
    status: WorkItemStatus
    priority: WorkItemPriority
    due_date: date | None = None
    waiting_on: str | None = Field(default=None, max_length=1_000)
    blocker_note: str | None = Field(default=None, max_length=2_000)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    version: int = Field(ge=1)
    provenance: Provenance
    category: WorkCategory = WorkCategory.OTHER
    responsibility_domain: ResponsibilityDomain | None = None
    next_action_date: date | None = None
    requester_label: str | None = None
    waiting_party_kind: WaitingPartyKind | None = None
    waiting_party_label: str | None = None
    waiting_owed: str | None = None
    requested_date: date | None = None
    chase_date: date | None = None
    blocker_description: str | None = None
    resolution_owner: str | None = None
    review_date: date | None = None
    completion_outcome: str | None = None
    completion_evidence: str | None = None
    contribution_candidate: bool = False

    @field_validator("title")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = str(getattr(info, "field_name", "value"))
        return _trim_required(value, field_name)

    @field_validator("details", "waiting_on", "blocker_note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _trim_optional(value)

    @model_validator(mode="after")
    def validate_invariants(self) -> Self:
        if self.kind == WorkItemKind.MILESTONE and self.due_date is None:
            raise ValueError("MILESTONE requires a due_date")
        if self.status == WorkItemStatus.WAITING:
            if self.waiting_on is None:
                raise ValueError("WAITING requires waiting_on")
        elif self.waiting_on is not None:
            raise ValueError("waiting_on must be clear outside WAITING")
        if self.status == WorkItemStatus.BLOCKED:
            if self.blocker_note is None:
                raise ValueError("BLOCKED requires blocker_note")
        elif self.blocker_note is not None:
            raise ValueError("blocker_note must be clear outside BLOCKED")
        if self.status == WorkItemStatus.COMPLETED:
            if self.completed_at is None:
                raise ValueError("COMPLETED requires completed_at")
        elif self.completed_at is not None:
            raise ValueError("completed_at must be clear outside COMPLETED")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("work-item timestamps must be timezone-aware UTC values")
        if self.completed_at is not None and self.completed_at.tzinfo is None:
            raise ValueError("completed_at must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self
