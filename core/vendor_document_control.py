"""Typed deterministic contracts for bid-stage vendor-document compliance."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.schemas import Provenance


def _required(value: str, name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


def _optional(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(value.split()) or None


class TimingAnchor(StrEnum):
    ANTICIPATED_AWARD = "ANTICIPATED_AWARD"
    FORECAST_DELIVERY = "FORECAST_DELIVERY"
    OTHER = "OTHER"


class VerificationStatus(StrEnum):
    NOT_REVIEWED = "NOT_REVIEWED"
    AWAITING_MANUFACTURER = "AWAITING_MANUFACTURER"
    CONFIRMED_COMPLIANT = "CONFIRMED_COMPLIANT"
    CONFIRMED_WITH_EXCEPTION = "CONFIRMED_WITH_EXCEPTION"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    CANNOT_COMPLY = "CANNOT_COMPLY"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CommercialImpact(StrEnum):
    NONE = "NONE"
    INCLUDED = "INCLUDED"
    EXCLUDED = "EXCLUDED"
    ALLOWANCE = "ALLOWANCE"
    UNKNOWN = "UNKNOWN"


class BidDisposition(StrEnum):
    NONE = "NONE"
    ACCEPTED = "ACCEPTED"
    MODIFIED = "MODIFIED"
    QUALIFIED = "QUALIFIED"
    EXCLUDED = "EXCLUDED"


class ReadinessAttention(StrEnum):
    NOT_REVIEWED = "NOT_REVIEWED"
    AWAITING_MANUFACTURER = "AWAITING_MANUFACTURER"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    CANNOT_COMPLY_UNRESOLVED = "CANNOT_COMPLY_UNRESOLVED"
    EXCEPTION_UNRESOLVED = "EXCEPTION_UNRESOLVED"
    MISSING_MANUFACTURER = "MISSING_MANUFACTURER"
    MISSING_OWNER = "MISSING_OWNER"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    UNKNOWN_COMMERCIAL_IMPACT = "UNKNOWN_COMMERCIAL_IMPACT"


class TemplateStage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(max_length=40)
    label: str = Field(max_length=200)
    help_text: str = Field(max_length=1000)

    @field_validator("code", "label", "help_text")
    @classmethod
    def normalize_required(cls, value: str, info: object) -> str:
        return _required(value, str(getattr(info, "field_name", "value")))


class VdrlTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    template_id: str
    name: str
    version: int = Field(ge=1)
    stages: list[TemplateStage]
    provenance: Provenance
    created_at: datetime


class SupplierPackageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bid_id: str
    package_name: str = Field(max_length=300)
    package_code: str = Field(max_length=100)
    customer_epcm: str | None = Field(default=None, max_length=300)
    proposed_manufacturer: str = Field(max_length=300)
    manufacturer_contact: str | None = Field(default=None, max_length=500)
    internal_owner: str = Field(max_length=300)
    source_vdrl_reference: str | None = Field(default=None, max_length=300)
    source_revision: str | None = Field(default=None, max_length=100)
    anticipated_award_date: date | None = None
    forecast_delivery_date: date | None = None
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator(
        "bid_id", "package_name", "package_code", "proposed_manufacturer", "internal_owner"
    )
    @classmethod
    def normalize_required(cls, value: str, info: object) -> str:
        return _required(value, str(getattr(info, "field_name", "value")))

    @field_validator(
        "customer_epcm",
        "manufacturer_contact",
        "source_vdrl_reference",
        "source_revision",
        "notes",
    )
    @classmethod
    def normalize_optional(cls, value: str | None) -> str | None:
        return _optional(value)


class SupplierPackage(SupplierPackageCreate):
    package_id: str
    template_id: str
    template_version: int
    version_token: str
    provenance: Provenance
    created_at: datetime
    updated_at: datetime


class CustomerRequirementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: str
    customer_requirement_code: str = Field(max_length=100)
    deliverable_title: str = Field(max_length=500)
    description: str = Field(default="", max_length=5000)
    required: bool = True
    requested_stages: list[str] = Field(default_factory=list)
    timing_anchor: TimingAnchor = TimingAnchor.OTHER
    timing_offset_days: int = Field(default=0, ge=-3650, le=3650)
    original_contractual_timing: str | None = Field(default=None, max_length=1000)
    original_contractual_date: date | None = None
    customer_notes: str | None = Field(default=None, max_length=5000)
    source_row_reference: str | None = Field(default=None, max_length=500)
    source_revision: str | None = Field(default=None, max_length=100)
    applicable: bool = True

    @field_validator("package_id", "customer_requirement_code", "deliverable_title")
    @classmethod
    def normalize_required(cls, value: str, info: object) -> str:
        return _required(value, str(getattr(info, "field_name", "value")))

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator(
        "original_contractual_timing",
        "customer_notes",
        "source_row_reference",
        "source_revision",
    )
    @classmethod
    def normalize_optional(cls, value: str | None) -> str | None:
        return _optional(value)

    @field_validator("requested_stages")
    @classmethod
    def normalize_stages(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            stage = _required(item, "requested stage").upper()
            if stage not in normalized:
                normalized.append(stage)
        return normalized


class ManufacturerVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_status: VerificationStatus = VerificationStatus.NOT_REVIEWED
    proposed_manufacturer: str | None = Field(default=None, max_length=300)
    response_source: str | None = Field(default=None, max_length=1000)
    internal_owner: str | None = Field(default=None, max_length=300)
    confirmation_requested_date: date | None = None
    response_received_date: date | None = None
    committed_stages: list[str] = Field(default_factory=list)
    committed_timing: str | None = Field(default=None, max_length=2000)
    evidence_reference: str | None = Field(default=None, max_length=2000)
    manufacturer_notes: str | None = Field(default=None, max_length=5000)
    proposed_exception: str | None = Field(default=None, max_length=5000)
    commercial_impact: CommercialImpact = CommercialImpact.NONE
    bid_disposition: BidDisposition = BidDisposition.NONE
    disposition_approved: bool = False
    clarification_reference: str | None = Field(default=None, max_length=500)
    deviation_reference: str | None = Field(default=None, max_length=500)
    unresolved_action: str | None = Field(default=None, max_length=2000)
    handover_note: str | None = Field(default=None, max_length=5000)

    @field_validator(
        "proposed_manufacturer",
        "response_source",
        "internal_owner",
        "committed_timing",
        "evidence_reference",
        "manufacturer_notes",
        "proposed_exception",
        "clarification_reference",
        "deviation_reference",
        "unresolved_action",
        "handover_note",
    )
    @classmethod
    def normalize_optional(cls, value: str | None) -> str | None:
        return _optional(value)

    @field_validator("committed_stages")
    @classmethod
    def normalize_stages(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            stage = _required(item, "committed stage").upper()
            if stage not in normalized:
                normalized.append(stage)
        return normalized

    @model_validator(mode="after")
    def validate_status_fields(self) -> Self:
        received_statuses = {
            VerificationStatus.CONFIRMED_COMPLIANT,
            VerificationStatus.CONFIRMED_WITH_EXCEPTION,
            VerificationStatus.CANNOT_COMPLY,
            VerificationStatus.NOT_APPLICABLE,
        }
        if self.verification_status in received_statuses and self.response_received_date is None:
            raise ValueError("a received manufacturer response requires response received date")
        if (
            self.verification_status is VerificationStatus.CONFIRMED_WITH_EXCEPTION
            and self.proposed_exception is None
        ):
            raise ValueError(
                "confirmed with exception requires a proposed modification or exception"
            )
        if self.verification_status is VerificationStatus.CANNOT_COMPLY:
            if self.manufacturer_notes is None and self.proposed_exception is None:
                raise ValueError("cannot comply requires a reason")
            if self.bid_disposition is BidDisposition.NONE:
                raise ValueError("cannot comply requires a bid disposition")
        if (
            self.verification_status is VerificationStatus.CLARIFICATION_REQUIRED
            and self.unresolved_action is None
        ):
            raise ValueError("clarification required needs a question or action")
        if (
            self.verification_status is VerificationStatus.NOT_APPLICABLE
            and self.manufacturer_notes is None
        ):
            raise ValueError("not applicable requires a reason")
        if self.disposition_approved and self.bid_disposition is BidDisposition.NONE:
            raise ValueError("approved disposition requires a bid disposition")
        return self


class RequirementVerificationUpdate(ManufacturerVerification):
    expected_version: int = Field(ge=1)


class CustomerRequirement(CustomerRequirementCreate, ManufacturerVerification):
    requirement_id: str
    version: int = Field(ge=1)
    provenance: Provenance
    created_at: datetime
    updated_at: datetime


class BulkRequirementTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    expected_version: int = Field(ge=1)


class BulkVerificationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[BulkRequirementTarget]
    proposed_manufacturer: str | None = Field(default=None, max_length=300)
    internal_owner: str | None = Field(default=None, max_length=300)
    verification_status: VerificationStatus | None = None

    @field_validator("proposed_manufacturer", "internal_owner")
    @classmethod
    def normalize_optional(cls, value: str | None) -> str | None:
        return _optional(value)

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        if not self.targets:
            raise ValueError("select at least one requirement")
        ids = [target.requirement_id for target in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate requirement selection")
        if (
            self.proposed_manufacturer is None
            and self.internal_owner is None
            and self.verification_status is None
        ):
            raise ValueError("choose at least one bulk change")
        if self.verification_status not in {
            None,
            VerificationStatus.NOT_REVIEWED,
            VerificationStatus.AWAITING_MANUFACTURER,
        }:
            raise ValueError("bulk status is limited to statuses without response-specific fields")
        return self


class RequirementBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str
    code: ReadinessAttention
    explanation: str


class ReadinessCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_applicable: int
    not_reviewed: int
    awaiting_manufacturer: int
    confirmed_compliant: int
    confirmed_with_exception: int
    clarification_required: int
    cannot_comply: int
    unknown_commercial_impact: int
    ready_for_handover: int


class VdrlReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ready: bool
    counts: ReadinessCounts
    blockers: list[RequirementBlocker]


class ImportRowResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    row_number: int
    values: dict[str, str]
    errors: list[str] = Field(default_factory=list)


class ImportPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rows: list[ImportRowResult]
    declared_required_total: int | None
    actual_required_total: int
    errors: list[str] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors and all(not row.errors for row in self.rows)


def calculate_requested_date(
    package: SupplierPackage,
    anchor: TimingAnchor,
    offset_days: int,
) -> date | None:
    """Calculate a bid-stage requested date from the preserved configured rule."""
    base = {
        TimingAnchor.ANTICIPATED_AWARD: package.anticipated_award_date,
        TimingAnchor.FORECAST_DELIVERY: package.forecast_delivery_date,
        TimingAnchor.OTHER: None,
    }[anchor]
    return base + timedelta(days=offset_days) if base is not None else None


def _add_blocker(
    blockers: list[RequirementBlocker],
    requirement_id: str,
    code: ReadinessAttention,
    explanation: str,
) -> None:
    blockers.append(
        RequirementBlocker(
            requirement_id=requirement_id,
            code=code,
            explanation=explanation,
        )
    )


def readiness_for(requirements: list[CustomerRequirement]) -> VdrlReadiness:
    applicable = [item for item in requirements if item.applicable and item.required]
    blockers: list[RequirementBlocker] = []
    for item in applicable:
        if item.verification_status is VerificationStatus.NOT_REVIEWED:
            _add_blocker(
                blockers,
                item.requirement_id,
                ReadinessAttention.NOT_REVIEWED,
                "Manufacturer verification has not started",
            )
        if item.verification_status is VerificationStatus.AWAITING_MANUFACTURER:
            _add_blocker(
                blockers,
                item.requirement_id,
                ReadinessAttention.AWAITING_MANUFACTURER,
                "Manufacturer response is outstanding",
            )
        if item.verification_status is VerificationStatus.CLARIFICATION_REQUIRED:
            _add_blocker(
                blockers,
                item.requirement_id,
                ReadinessAttention.CLARIFICATION_REQUIRED,
                item.unresolved_action or "Clarification is unresolved",
            )
        if item.verification_status is VerificationStatus.CANNOT_COMPLY and not (
            item.bid_disposition is not BidDisposition.NONE and item.disposition_approved
        ):
            _add_blocker(
                blockers,
                item.requirement_id,
                ReadinessAttention.CANNOT_COMPLY_UNRESOLVED,
                "Cannot-comply response lacks an approved bid disposition",
            )
        if item.verification_status is VerificationStatus.CONFIRMED_WITH_EXCEPTION and (
            item.bid_disposition is BidDisposition.NONE or not item.disposition_approved
        ):
            _add_blocker(
                blockers,
                item.requirement_id,
                ReadinessAttention.EXCEPTION_UNRESOLVED,
                "Manufacturer exception lacks an approved bid disposition",
            )
        if item.proposed_manufacturer is None:
            _add_blocker(
                blockers,
                item.requirement_id,
                ReadinessAttention.MISSING_MANUFACTURER,
                "Proposed manufacturer is missing",
            )
        if item.internal_owner is None:
            _add_blocker(
                blockers,
                item.requirement_id,
                ReadinessAttention.MISSING_OWNER,
                "Internal owner is missing",
            )
        if (
            item.verification_status
            in {
                VerificationStatus.CONFIRMED_COMPLIANT,
                VerificationStatus.CONFIRMED_WITH_EXCEPTION,
                VerificationStatus.CANNOT_COMPLY,
            }
            and item.evidence_reference is None
        ):
            _add_blocker(
                blockers,
                item.requirement_id,
                ReadinessAttention.MISSING_EVIDENCE,
                "Manufacturer response evidence is missing",
            )
        if (
            item.verification_status
            in {
                VerificationStatus.CONFIRMED_WITH_EXCEPTION,
                VerificationStatus.CANNOT_COMPLY,
            }
            and item.commercial_impact is CommercialImpact.UNKNOWN
        ):
            _add_blocker(
                blockers,
                item.requirement_id,
                ReadinessAttention.UNKNOWN_COMMERCIAL_IMPACT,
                "Commercial impact remains unknown",
            )
    statuses = [item.verification_status for item in applicable]
    blocked_ids = {blocker.requirement_id for blocker in blockers}
    counts = ReadinessCounts(
        total_applicable=len(applicable),
        not_reviewed=statuses.count(VerificationStatus.NOT_REVIEWED),
        awaiting_manufacturer=statuses.count(VerificationStatus.AWAITING_MANUFACTURER),
        confirmed_compliant=statuses.count(VerificationStatus.CONFIRMED_COMPLIANT),
        confirmed_with_exception=statuses.count(VerificationStatus.CONFIRMED_WITH_EXCEPTION),
        clarification_required=statuses.count(VerificationStatus.CLARIFICATION_REQUIRED),
        cannot_comply=statuses.count(VerificationStatus.CANNOT_COMPLY),
        unknown_commercial_impact=sum(
            item.commercial_impact is CommercialImpact.UNKNOWN for item in applicable
        ),
        ready_for_handover=sum(item.requirement_id not in blocked_ids for item in applicable),
    )
    return VdrlReadiness(ready=bool(applicable) and not blockers, counts=counts, blockers=blockers)
