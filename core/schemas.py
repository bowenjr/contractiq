"""Pydantic schemas for ContractIQ's deterministic bid-management spine."""

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.enums import (
    Actor,
    ApprovalType,
    BidLevel,
    BidStatus,
    CustomerType,
    Gate,
    GateStatus,
    InferencePolicy,
    RiskTrigger,
)


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_by: Actor
    agent_name: str | None = None
    model: str | None = None
    source_document_id: str | None = None
    source_location: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    human_confirmed: bool = False
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_human_confirmation(self) -> Self:
        if self.human_confirmed and self.confirmed_by is None:
            raise ValueError("confirmed_by is required when human_confirmed is true")
        return self

    @classmethod
    def from_ai(
        cls,
        agent_name: str,
        model: str,
        source_document_id: str | None = None,
        source_location: str | None = None,
    ) -> Self:
        return cls(
            created_by=Actor.AI,
            agent_name=agent_name,
            model=model,
            source_document_id=source_document_id,
            source_location=source_location,
            human_confirmed=False,
        )

    @classmethod
    def from_human(cls, who: str) -> Self:
        now = datetime.now(UTC)
        return cls(
            created_by=Actor.HUMAN,
            agent_name=who,
            created_at=now,
            human_confirmed=True,
            confirmed_by=who,
            confirmed_at=now,
        )


class Bid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bid_id: str = Field(pattern=r"^B-\d{4}-\d{4}$")
    customer: str
    customer_type: CustomerType
    project_name: str
    location: str | None = None
    sales_owner: str
    bc_owner: str
    executive_sponsor: str | None = None
    release_date: date
    customer_due_date: date
    customer_due_time: time | None = None
    """Local closing time the customer's document states. ``None`` when not recorded."""
    customer_due_timezone: str | None = None
    """IANA zone the closing time is in, such as ``America/Toronto``. Set with the time."""
    internal_due_date: date
    anticipated_award_date: date | None = None
    estimated_value: Decimal
    currency: str = "CAD"
    margin_range: str | None = None
    win_probability: int | None = Field(default=None, ge=0, le=100)
    classification: BidLevel
    current_gate: Gate = Gate.G0
    status: BidStatus = BidStatus.ACTIVE
    risk_triggers: list[RiskTrigger] = Field(default_factory=list)
    inference_policy: InferencePolicy = InferencePolicy.LOCAL_ONLY
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_internal_due_date(self) -> Self:
        if self.internal_due_date > self.customer_due_date:
            raise ValueError("internal_due_date must be on or before customer_due_date")
        return self

    @model_validator(mode="after")
    def validate_customer_closing(self) -> Self:
        if (self.customer_due_time is None) != (self.customer_due_timezone is None):
            raise ValueError(
                "customer closing time and time zone are recorded together: enter both "
                "(for example 12:00 and America/Toronto) or leave both blank"
            )
        if self.customer_due_timezone is not None:
            try:
                ZoneInfo(self.customer_due_timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError(
                    f"unknown time zone {self.customer_due_timezone!r}; use an IANA name such "
                    "as America/Toronto"
                ) from exc
        return self

    def customer_closing_instant(self) -> datetime | None:
        """The customer's closing moment in UTC, or ``None`` unless time and zone are recorded."""
        if self.customer_due_time is None or self.customer_due_timezone is None:
            return None
        local = datetime.combine(
            self.customer_due_date, self.customer_due_time, ZoneInfo(self.customer_due_timezone)
        )
        return local.astimezone(UTC)

    def customer_closing_label(self) -> str:
        """The closing date, with its local time and zone when recorded, else saying it is not."""
        if self.customer_due_time is None or self.customer_due_timezone is None:
            return f"{self.customer_due_date.isoformat()} (closing time not recorded)"
        local = datetime.combine(
            self.customer_due_date, self.customer_due_time, ZoneInfo(self.customer_due_timezone)
        )
        return (
            f"{self.customer_due_date.isoformat()} {self.customer_due_time.strftime('%H:%M')} "
            f"{local.tzname()} ({self.customer_due_timezone})"
        )


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str
    bid_id: str
    approval_type: ApprovalType
    required: bool = True
    obtained: bool = False
    authority: str | None = None
    evidence_ref: str | None = None
    decision: str | None = None
    decided_at: datetime | None = None
    provenance: Provenance
    # The immutable scenario-baseline selection this approval authorized. NULL means
    # the historical row predates baseline binding and cannot be current authority.
    pricing_baseline_id: str | None = None
    # Append-only lineage: a later decision supersedes (never edits) an earlier one.
    supersedes_approval_id: str | None = None
    recorded_at: datetime | None = None


class GateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bid_id: str
    gate: Gate
    status: GateStatus = GateStatus.NOT_STARTED
    blockers: list[str] = Field(default_factory=list)
    override_by: str | None = None
    override_risk_note: str | None = None
    decided_at: datetime | None = None

    @model_validator(mode="after")
    def validate_override(self) -> Self:
        if self.status == GateStatus.OVERRIDDEN and (
            self.override_by is None or self.override_risk_note is None
        ):
            raise ValueError(
                "override_by and override_risk_note are required when status is overridden"
            )
        return self


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    bid_id: str | None
    actor: str
    action: str
    detail: str
    timestamp: datetime
