"""Typed OPS-09 proposal-readiness and issued-offer control records."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProposalControlStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    READY_FOR_EXPORT = "READY_FOR_EXPORT"
    EXPORTED = "EXPORTED"
    GENERATED_ARTIFACTS_RECEIVED = "GENERATED_ARTIFACTS_RECEIVED"
    STALE = "STALE"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED_FOR_ISSUE = "APPROVED_FOR_ISSUE"
    ISSUED = "ISSUED"
    SUPERSEDED = "SUPERSEDED"
    BLOCKED = "BLOCKED"


class ProposalBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    area: str
    message: str
    destination: str
    record_id: str | None = None


class ProposalReadinessAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bid_id: str
    status: ProposalControlStatus
    blockers: tuple[ProposalBlocker, ...] = ()
    changed_areas: tuple[str, ...] = ()
    current_export_id: str | None = None
    candidate_id: str | None = None
    candidate_version: int | None = None
    manifest_valid: bool = False
    artifacts_verified: bool = False
    approvals_clear: bool = False
    next_action: str
    next_action_path: str


class ProposalExportRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    export_id: str
    package_id: str
    bid_id: str
    contract_version: str
    source_projection_sha256: str
    canonical_sha256: str
    canonical_package_json: str
    area_hashes: dict[str, str]
    supersedes_export_id: str | None = None
    created_by: str
    created_at: datetime


class ImportedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    receipt_id: str
    role: str
    filename: str
    media_type: str
    byte_size: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    storage_path: str


class ManifestReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_id: str
    export_id: str
    bid_id: str
    proposal_id: str
    proposal_revision: str
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_json: str
    generation_status: str
    generated_at: datetime
    imported_by: str
    imported_at: datetime
    artifacts: tuple[ImportedArtifact, ...] = ()


class ProposalIssueCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    bid_id: str
    export_id: str
    receipt_id: str
    status: ProposalControlStatus
    version: int = Field(ge=1)
    supersedes_candidate_id: str | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime


class CustomerIssueCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    expected_version: int = Field(ge=1)
    issue_revision: str = Field(min_length=1, max_length=64)
    issued_at: datetime
    issue_method: str = Field(min_length=1, max_length=128)
    destination_reference: str = Field(min_length=1, max_length=512)
    offer_valid_until: date | None = None
    note: str = Field(default="", max_length=4000)


class IssuedOfferBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_id: str
    issue_id: str
    bid_id: str
    candidate_id: str
    issue_revision: str
    issued_at: datetime
    issue_method: str
    destination_reference: str
    offer_valid_until: date | None
    note: str
    export_id: str
    package_id: str
    package_sha256: str
    receipt_id: str
    manifest_sha256: str
    snapshot: dict[str, Any]
    successor_of_baseline_id: str | None
    actor: str
    recorded_at: datetime


class ProposalControlHistory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exports: tuple[ProposalExportRecord, ...] = ()
    receipts: tuple[ManifestReceipt, ...] = ()
    candidates: tuple[ProposalIssueCandidate, ...] = ()
    baselines: tuple[IssuedOfferBaseline, ...] = ()
