"""TASK-16 deterministic commercial scenario models and arithmetic."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScenarioPurpose(StrEnum):
    BASE = "BASE"
    TARGET = "TARGET"
    DOWNSIDE = "DOWNSIDE"
    ALTERNATIVE = "ALTERNATIVE"
    NEGOTIATED = "NEGOTIATED"
    AWARD = "AWARD"


class ScenarioLifecycle(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    WITHDRAWN = "WITHDRAWN"


class VersionState(StrEnum):
    DRAFT = "DRAFT"
    CALCULATED = "CALCULATED"
    REVIEW_ACCEPTED = "REVIEW_ACCEPTED"
    REVIEW_CHANGES_REQUIRED = "REVIEW_CHANGES_REQUIRED"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    HISTORICAL = "HISTORICAL"


class CashDirection(StrEnum):
    INFLOW = "INFLOW"
    OUTFLOW = "OUTFLOW"
    NONCASH = "NONCASH"


class ReviewDecision(StrEnum):
    ACCEPTED = "ACCEPTED"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    REJECTED = "REJECTED"


class ScenarioFamily(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family_id: str = Field(default_factory=lambda: f"SCF-{uuid4().hex}")
    bid_id: str
    code: str = Field(min_length=1, max_length=80)
    purpose: ScenarioPurpose
    title: str
    owner: str
    intent: str
    lifecycle: ScenarioLifecycle = ScenarioLifecycle.DRAFT
    materiality: str = "UNASSESSED"
    decision_date: date | None = None
    version: int = 1
    created_by: str
    created_at: datetime


class ScenarioSourceLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    link_id: str = Field(default_factory=lambda: f"SSL-{uuid4().hex}")
    bid_id: str
    scenario_version_id: str
    source_type: str
    source_id: str
    source_version_id: str
    treatment: str = "INCLUDED"
    exact_amount: Decimal | None = None
    currency: str | None = None
    rationale: str | None = None

    @model_validator(mode="after")
    def validate_amount(self) -> Self:
        if self.exact_amount is not None and not self.currency:
            raise ValueError("exact source amounts require an explicit currency")
        return self


class ScenarioLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_id: str = Field(default_factory=lambda: f"SCL-{uuid4().hex}")
    role: str
    amount: Decimal
    currency: str
    contributes_to_revenue: bool = False
    contributes_to_cost: bool = False
    rationale: str | None = None


class ScenarioAssumption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assumption_id: str = Field(default_factory=lambda: f"ASM-{uuid4().hex}")
    assumption_type: str
    value: Decimal | str
    unit: str
    effective_date: date
    rationale: str

    @model_validator(mode="after")
    def reject_float(self) -> Self:
        if isinstance(self.value, float):
            raise ValueError("authoritative assumptions cannot use float")
        return self


class CashEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(default_factory=lambda: f"CFE-{uuid4().hex}")
    event_date: date
    direction: CashDirection
    amount: Decimal
    currency: str
    event_type: str
    rationale: str


class ScenarioVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_version_id: str = Field(default_factory=lambda: f"SCV-{uuid4().hex}")
    family_id: str
    bid_id: str
    version_number: int = Field(ge=1)
    state: VersionState = VersionState.DRAFT
    presentation_currency: str
    decimal_scale: int = Field(default=2, ge=0, le=8)
    day_count_convention: str = "ACTUAL_365"
    lines: tuple[ScenarioLine, ...]
    assumptions: tuple[ScenarioAssumption, ...] = ()
    cash_events: tuple[CashEvent, ...] = ()
    source_links: tuple[ScenarioSourceLink, ...]
    created_by: str
    created_at: datetime
    fingerprint: str | None = None

    @model_validator(mode="after")
    def fingerprint_value(self) -> Self:
        canonical = json.dumps(
            self.model_dump(mode="json", exclude={"fingerprint"}),
            sort_keys=True,
            separators=(",", ":"),
        )
        expected = sha256(canonical.encode()).hexdigest()
        if self.fingerprint is not None and self.fingerprint != expected:
            raise ValueError("scenario fingerprint mismatch")
        object.__setattr__(self, "fingerprint", expected)
        return self


class ScenarioResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_version_id: str
    presentation_currency: str
    revenue: Decimal
    total_cost: Decimal
    gross_profit: Decimal
    gross_margin_bps: int | None
    markup_bps: int | None
    cumulative_cash_flow: tuple[tuple[date, Decimal], ...]
    peak_working_capital: Decimal
    fingerprint: str


class ScenarioReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: str = Field(default_factory=lambda: f"SCR-{uuid4().hex}")
    scenario_version_id: str
    decision: ReviewDecision
    reviewer: str
    reviewed_at: datetime
    rationale: str


class ScenarioComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    comparison_id: str = Field(default_factory=lambda: f"SCP-{uuid4().hex}")
    bid_id: str
    base_version_id: str
    compared_version_id: str
    revenue_delta: Decimal
    cost_delta: Decimal
    gross_profit_delta: Decimal
    fingerprint: str


class BaselineSelection(BaseModel):
    model_config = ConfigDict(frozen=True)

    selection_id: str = Field(default_factory=lambda: f"SBL-{uuid4().hex}")
    bid_id: str
    scenario_version_id: str
    selected_by: str
    selected_at: datetime
    rationale: str


def quantize(value: Decimal, scale: int = 2) -> Decimal:
    """Round Decimal values using the explicit scenario scale."""
    return value.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)


def calculate_scenario(version: ScenarioVersion) -> ScenarioResult:
    """Pure deterministic calculation with no I/O, clock, float, or defaults."""
    revenue = quantize(
        sum((line.amount for line in version.lines if line.contributes_to_revenue), Decimal()),
        version.decimal_scale,
    )
    cost = quantize(
        sum((line.amount for line in version.lines if line.contributes_to_cost), Decimal()),
        version.decimal_scale,
    )
    profit = quantize(revenue - cost, version.decimal_scale)
    margin = int((profit * Decimal(10000) / revenue).to_integral_value()) if revenue else None
    markup = int((profit * Decimal(10000) / cost).to_integral_value()) if cost else None
    balance = Decimal()
    points: list[tuple[date, Decimal]] = []
    for event in sorted(version.cash_events, key=lambda item: (item.event_date, item.event_id)):
        if event.direction == CashDirection.INFLOW:
            balance += event.amount
        elif event.direction == CashDirection.OUTFLOW:
            balance -= event.amount
        points.append((event.event_date, quantize(balance, version.decimal_scale)))
    peak = quantize(
        max(((-value for _, value in points if value < 0)), default=Decimal()),
        version.decimal_scale,
    )
    payload = (
        f"{version.scenario_version_id}|{revenue}|{cost}|{profit}|{margin}|{markup}|{points}|{peak}"
    )
    return ScenarioResult(
        scenario_version_id=version.scenario_version_id,
        presentation_currency=version.presentation_currency,
        revenue=revenue,
        total_cost=cost,
        gross_profit=profit,
        gross_margin_bps=margin,
        markup_bps=markup,
        cumulative_cash_flow=tuple(points),
        peak_working_capital=peak,
        fingerprint=sha256(payload.encode()).hexdigest(),
    )
