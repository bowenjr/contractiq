# ruff: noqa: UP042
"""Authoritative TASK-10 scope and interface domain contracts.

This module is deliberately free of persistence and web concerns so the gap and
coverage projections can be tested deterministically.
"""

from datetime import date, datetime
from enum import Enum
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.schemas import Provenance


class ScopeArea(str, Enum):
    CORE_PRODUCTS = "CORE_PRODUCTS"
    ACCESSORIES = "ACCESSORIES"
    INTERFACES = "INTERFACES"
    SPARES = "SPARES"
    ENGINEERING = "ENGINEERING"
    TESTING_INSPECTION = "TESTING_INSPECTION"
    DOCUMENTS = "DOCUMENTS"
    PACKAGING_LOGISTICS = "PACKAGING_LOGISTICS"
    SERVICES = "SERVICES"
    WARRANTY = "WARRANTY"
    CUSTOMER_RESPONSIBILITIES = "CUSTOMER_RESPONSIBILITIES"
    EXCLUSIONS_ASSUMPTIONS = "EXCLUSIONS_ASSUMPTIONS"
    SCHEDULE = "SCHEDULE"
    COMMERCIAL_INTERFACES = "COMMERCIAL_INTERFACES"
    CLOSEOUT = "CLOSEOUT"


class CustomerNeed(str, Enum):
    UNASSESSED = "UNASSESSED"
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    NOT_REQUIRED = "NOT_REQUIRED"
    UNCLEAR = "UNCLEAR"


class OfferPosition(str, Enum):
    UNDECIDED = "UNDECIDED"
    INCLUDED = "INCLUDED"
    EXCLUDED = "EXCLUDED"
    OPTION = "OPTION"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PricingState(str, Enum):
    UNCONFIRMED = "UNCONFIRMED"
    PRICED = "PRICED"
    ALLOWANCED = "ALLOWANCED"
    NO_CHARGE = "NO_CHARGE"
    NOT_PRICED = "NOT_PRICED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Materiality(str, Enum):
    UNASSESSED = "UNASSESSED"
    MATERIAL = "MATERIAL"
    NON_MATERIAL = "NON_MATERIAL"


class WorkState(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    COMPLETE = "COMPLETE"


class ReviewState(str, Enum):
    NOT_REVIEWED = "NOT_REVIEWED"
    ACCEPTED = "ACCEPTED"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"


class LifecycleState(str, Enum):
    ACTIVE = "ACTIVE"
    WITHDRAWN = "WITHDRAWN"


class ScopeOrigin(str, Enum):
    REQUIREMENT_DERIVED = "REQUIREMENT_DERIVED"
    INTERNAL = "INTERNAL"


class ScopeRelevance(str, Enum):
    UNASSESSED = "UNASSESSED"
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DependencyState(str, Enum):
    OPEN = "OPEN"
    SATISFIED = "SATISFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


TEXT_MAX = 5000


def _clean(value: str | None, name: str, max_length: int = TEXT_MAX) -> str | None:
    if value is None:
        return None
    result = value.strip()
    if not result:
        return None
    if len(result) > max_length:
        raise ValueError(f"{name} exceeds maximum length")
    return result


class ScopeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_item_id: str = Field(default_factory=lambda: f"SCOPE-{uuid4().hex}")
    bid_id: str
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=5000)
    scope_area: ScopeArea
    origin: ScopeOrigin
    customer_need: CustomerNeed = CustomerNeed.UNASSESSED
    offer_position: OfferPosition = OfferPosition.UNDECIDED
    pricing_state: PricingState = PricingState.UNCONFIRMED
    responsible_party: str | None = None
    owner: str | None = None
    due_date: date | None = None
    materiality: Materiality = Materiality.UNASSESSED
    assumption_exclusion_note: str | None = None
    evidence_decision_note: str | None = None
    work_state: WorkState = WorkState.OPEN
    review_state: ReviewState = ReviewState.NOT_REVIEWED
    reviewer: str | None = None
    review_note: str | None = None
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    created_at: datetime
    updated_at: datetime
    version: int = Field(default=1, ge=1)
    provenance: Provenance
    created_by: str

    @model_validator(mode="after")
    def validate_coherence(self) -> Self:
        if (
            self.offer_position == OfferPosition.INCLUDED
            and self.pricing_state == PricingState.NOT_APPLICABLE
        ):
            raise ValueError("included scope cannot have not-applicable pricing")
        if (
            self.offer_position == OfferPosition.NOT_APPLICABLE
            and self.pricing_state != PricingState.NOT_APPLICABLE
        ):
            raise ValueError("not-applicable offer requires not-applicable pricing")
        if (
            self.customer_need == CustomerNeed.REQUIRED
            and self.offer_position == OfferPosition.NOT_APPLICABLE
        ):
            raise ValueError("required scope cannot be not-applicable")
        if self.offer_position == OfferPosition.NOT_APPLICABLE and not (
            self.assumption_exclusion_note or self.evidence_decision_note
        ):
            raise ValueError("not-applicable offer requires rationale")
        if self.review_state == ReviewState.ACCEPTED and (
            not self.reviewer
            or self.work_state not in (WorkState.READY_FOR_REVIEW, WorkState.COMPLETE)
        ):
            raise ValueError("accepted scope requires reviewer and reviewable work")
        if self.work_state == WorkState.COMPLETE:
            if (
                self.customer_need in (CustomerNeed.UNASSESSED, CustomerNeed.UNCLEAR)
                or self.materiality == Materiality.UNASSESSED
                or self.offer_position == OfferPosition.UNDECIDED
            ):
                raise ValueError("complete scope has unresolved assessment")
            if self.customer_need == CustomerNeed.REQUIRED and not self.responsible_party:
                raise ValueError("required complete scope needs responsible party")
            if self.offer_position == OfferPosition.INCLUDED and self.pricing_state in (
                PricingState.UNCONFIRMED,
                PricingState.NOT_PRICED,
            ):
                raise ValueError("included complete scope needs resolved pricing")
        for field in (
            "responsible_party",
            "owner",
            "reviewer",
            "review_note",
            "assumption_exclusion_note",
            "evidence_decision_note",
        ):
            value = getattr(self, field)
            if value is not None:
                setattr(self, field, _clean(value, field))
        self.title = self.title.strip()
        self.description = self.description.strip()
        self.bid_id = self.bid_id.strip()
        return self

    @property
    def fully_closed(self) -> bool:
        return (
            self.lifecycle_state == LifecycleState.ACTIVE
            and self.work_state == WorkState.COMPLETE
            and self.review_state == ReviewState.ACCEPTED
        )


class InterfaceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interface_id: str = Field(default_factory=lambda: f"INT-{uuid4().hex}")
    bid_id: str
    title: str = Field(min_length=1, max_length=300)
    boundary_description: str = Field(min_length=1, max_length=5000)
    upstream_party: str = Field(min_length=1, max_length=300)
    downstream_party: str = Field(min_length=1, max_length=300)
    dependency_description: str = Field(min_length=1, max_length=5000)
    owner: str | None = None
    due_date: date | None = None
    materiality: Materiality = Materiality.UNASSESSED
    dependency_state: DependencyState = DependencyState.OPEN
    not_applicable_rationale: str | None = None
    work_state: WorkState = WorkState.OPEN
    review_state: ReviewState = ReviewState.NOT_REVIEWED
    reviewer: str | None = None
    review_note: str | None = None
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    created_at: datetime
    updated_at: datetime
    version: int = Field(default=1, ge=1)
    provenance: Provenance
    created_by: str

    @model_validator(mode="after")
    def validate_coherence(self) -> Self:
        if self.upstream_party.strip().casefold() == self.downstream_party.strip().casefold():
            raise ValueError("upstream and downstream parties must differ")
        if (
            self.dependency_state == DependencyState.NOT_APPLICABLE
            and not self.not_applicable_rationale
        ):
            raise ValueError("not-applicable dependency requires rationale")
        if self.review_state == ReviewState.ACCEPTED and (
            not self.reviewer
            or self.work_state not in (WorkState.READY_FOR_REVIEW, WorkState.COMPLETE)
        ):
            raise ValueError("accepted interface requires reviewer and reviewable work")
        if self.work_state == WorkState.COMPLETE and self.dependency_state == DependencyState.OPEN:
            raise ValueError("complete interface cannot have open dependency")
        for field in ("owner", "reviewer", "review_note", "not_applicable_rationale"):
            value = getattr(self, field)
            if value is not None:
                setattr(self, field, _clean(value, field))
        return self

    @property
    def fully_closed(self) -> bool:
        return (
            self.lifecycle_state == LifecycleState.ACTIVE
            and self.work_state == WorkState.COMPLETE
            and self.review_state == ReviewState.ACCEPTED
        )


class ScopeItemCreate(ScopeItem):
    pass


class InterfaceCreate(InterfaceRecord):
    pass
