"""Deterministic TASK-17 negotiation, mandate, trade, and concession controls."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Applicability(StrEnum):
    NOT_ASSESSED = "NOT_ASSESSED"
    NEGOTIATION_REQUIRED = "NEGOTIATION_REQUIRED"
    NO_NEGOTIATION_REQUIRED = "NO_NEGOTIATION_REQUIRED"


class PlanLifecycle(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    WITHDRAWN = "WITHDRAWN"


class Priority(StrEnum):
    MUST_CHANGE = "MUST_CHANGE"
    SHOULD_CHANGE = "SHOULD_CHANGE"
    NICE_TO_CHANGE = "NICE_TO_CHANGE"


class PositionLevel(StrEnum):
    CUSTOMER_CURRENT = "CUSTOMER_CURRENT"
    OPENING = "OPENING"
    TARGET = "TARGET"
    FALLBACK_MINIMUM = "FALLBACK_MINIMUM"
    WALK_AWAY_OR_ESCALATE = "WALK_AWAY_OR_ESCALATE"
    TENTATIVE = "TENTATIVE"
    NEGOTIATED_FINAL = "NEGOTIATED_FINAL"


class TradeSide(StrEnum):
    GIVE = "GIVE"
    GET = "GET"


class TradeState(StrEnum):
    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    AUTHORIZED = "AUTHORIZED"
    OFFERED = "OFFERED"
    TENTATIVELY_AGREED = "TENTATIVELY_AGREED"
    COMMITTED = "COMMITTED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    SUPERSEDED = "SUPERSEDED"
    REVERSED = "REVERSED"


class MovementType(StrEnum):
    CUSTOMER_POSITION_RECORDED = "CUSTOMER_POSITION_RECORDED"
    COMPANY_OFFER_MADE = "COMPANY_OFFER_MADE"
    CUSTOMER_COUNTER_RECORDED = "CUSTOMER_COUNTER_RECORDED"
    TENTATIVE_UNDERSTANDING_RECORDED = "TENTATIVE_UNDERSTANDING_RECORDED"
    COMPANY_COMMITMENT_RECORDED = "COMPANY_COMMITMENT_RECORDED"
    CUSTOMER_REJECTION_RECORDED = "CUSTOMER_REJECTION_RECORDED"
    COMPANY_WITHDRAWAL_RECORDED = "COMPANY_WITHDRAWAL_RECORDED"
    SUPERSESSION_RECORDED = "SUPERSESSION_RECORDED"
    REVERSAL_RECORDED = "REVERSAL_RECORDED"


class ValueState(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    CLAIMED = "CLAIMED"
    EVIDENCED = "EVIDENCED"
    REJECTED = "REJECTED"


class ReviewDecision(StrEnum):
    ACCEPTED = "ACCEPTED"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    REJECTED = "REJECTED"


class NegotiationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(default_factory=lambda: f"NPL-{uuid4().hex}")
    bid_id: str
    code: str
    applicability: Applicability = Applicability.NOT_ASSESSED
    title: str
    owner: str
    lifecycle: PlanLifecycle = PlanLifecycle.DRAFT
    version: int = Field(default=1, ge=1)
    created_by: str
    created_at: datetime


class NegotiationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str = Field(default_factory=lambda: f"NIS-{uuid4().hex}")
    plan_id: str
    code: str
    priority: Priority
    owner: str
    customer_current: str
    opening: str
    target: str
    fallback_minimum: str
    walk_away_or_escalate: str
    rationale: str

    @model_validator(mode="after")
    def must_have_minimum(self) -> Self:
        if self.priority == Priority.MUST_CHANGE and (
            not self.fallback_minimum or not self.walk_away_or_escalate
        ):
            raise ValueError("MUST_CHANGE requires fallback and walk-away/escalation")
        return self


class PlanVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_version_id: str = Field(default_factory=lambda: f"NPV-{uuid4().hex}")
    plan_id: str
    bid_id: str
    version_number: int = Field(ge=1)
    issues: tuple[NegotiationIssue, ...]
    source_links: tuple[dict[str, str], ...] = ()
    created_by: str
    created_at: datetime
    fingerprint: str | None = None

    @model_validator(mode="after")
    def freeze_fingerprint(self) -> Self:
        payload = json.dumps(self.model_dump(mode="json", exclude={"fingerprint"}), sort_keys=True)
        expected = sha256(payload.encode()).hexdigest()
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("plan fingerprint mismatch")
        object.__setattr__(self, "fingerprint", expected)
        return self


class Mandate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mandate_id: str = Field(default_factory=lambda: f"NMD-{uuid4().hex}")
    plan_version_id: str
    bid_id: str
    authorized_actors: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    issue_codes: tuple[str, ...]
    limit_amount: Decimal | None = None
    currency: str | None = None
    starts_at: datetime
    ends_at: datetime
    route_id: str | None = None
    state: str = "DRAFT"

    @model_validator(mode="after")
    def validate_limit(self) -> Self:
        if self.limit_amount is not None and not self.currency:
            raise ValueError("mandate limits require currency")
        return self


class ConditionalTrade(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    trade_id: str = Field(default_factory=lambda: f"NTR-{uuid4().hex}")
    bid_id: str
    plan_version_id: str
    give: str
    get: str
    required_value: str
    value_state: ValueState = ValueState.CLAIMED
    state: TradeState = TradeState.PLANNED
    created_at: datetime


class NegotiationMovement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: str = Field(default_factory=lambda: f"NME-{uuid4().hex}")
    bid_id: str
    movement_type: MovementType
    issue_code: str
    actor: str
    text: str
    trade_id: str | None = None
    authority_id: str | None = None
    created_at: datetime


class Concession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    concession_id: str = Field(default_factory=lambda: f"NCO-{uuid4().hex}")
    bid_id: str
    issue_code: str
    version_number: int = Field(ge=1)
    amount: Decimal
    currency: str
    unit: str
    basis: str
    state: str = "OFFERED"
    mandate_id: str | None = None
    authority_event_id: str | None = None
    created_at: datetime


def validate_concession(
    concession: Concession, mandate: Mandate | None, actor: str, at: datetime
) -> None:
    """Reject offers lacking current explicit authority or exceeding its limit."""
    if mandate is None or mandate.state != "AUTHORIZED":
        raise ValueError("no authorized mandate")
    if actor not in mandate.authorized_actors or not (mandate.starts_at <= at <= mandate.ends_at):
        raise ValueError("actor or mandate window is not authorized")
    if concession.issue_code not in mandate.issue_codes or "OFFER" not in mandate.allowed_actions:
        raise ValueError("concession action is outside mandate")
    if (
        mandate.limit_amount is not None
        and concession.currency == mandate.currency
        and concession.amount > mandate.limit_amount
    ):
        raise ValueError("concession exceeds mandate limit")
