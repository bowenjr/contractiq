"""Service boundary for bid-stage VDRL verification and handover."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from core.bid_repository import BidRepository
from core.enums import Actor
from core.export_controls import csv_safe_row
from core.handover import assess_manufacturer_handover
from core.schemas import AuditEntry, Provenance
from core.vendor_document_control import (
    BidDisposition,
    BulkVerificationUpdate,
    CommercialImpact,
    CustomerRequirement,
    CustomerRequirementCreate,
    ImportPreview,
    ImportRowResult,
    ManufacturerVerification,
    ReadinessAttention,
    RequirementVerificationUpdate,
    SupplierPackage,
    SupplierPackageCreate,
    TemplateStage,
    TimingAnchor,
    VdrlReadiness,
    VdrlTemplate,
    VerificationStatus,
    calculate_requested_date,
    readiness_for,
)
from core.vendor_document_repository import (
    StaleVendorDocumentError,
    VendorDocumentNotFoundError,
    VendorDocumentRepository,
)

STANDARD_TEMPLATE_ID = "VDRL-SYSTEM-GENERIC-BID-V1"
STANDARD_TEMPLATE_NAME = "Generic bid-stage VDRL"


class VendorDocumentService:
    def __init__(
        self,
        repository: VendorDocumentRepository,
        bid_repository: BidRepository,
        *,
        now_factory: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self.repository = repository
        self.bid_repository = bid_repository
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4

    def _now(self) -> datetime:
        value = self._now_factory()
        if value.tzinfo is None:
            raise ValueError("clock must be timezone-aware")
        return value.astimezone(UTC)

    def _id(self, prefix: str) -> str:
        return f"{prefix}-{self._id_factory()}"

    @staticmethod
    def _actor(actor: str) -> str:
        value = actor.strip()
        if not value:
            raise ValueError("actor must be non-empty")
        return value

    def _provenance(self, actor: str, source: str) -> Provenance:
        now = self._now()
        return Provenance(
            created_by=Actor.HUMAN,
            agent_name=actor,
            source_location=source,
            created_at=now,
            human_confirmed=True,
            confirmed_by=actor,
            confirmed_at=now,
        )

    def _audit(self, bid_id: str, actor: str, action: str, detail: Mapping[str, Any]) -> AuditEntry:
        return AuditEntry(
            entry_id=self._id("AUD"),
            bid_id=bid_id,
            actor=actor,
            action=action,
            detail=json.dumps(detail, sort_keys=True, default=str),
            timestamp=self._now(),
        )

    def ensure_standard_template(self) -> VdrlTemplate:
        existing = self.repository.get_template(STANDARD_TEMPLATE_ID)
        if existing is not None:
            return existing
        now = self._now()
        template = VdrlTemplate(
            template_id=STANDARD_TEMPLATE_ID,
            name=STANDARD_TEMPLATE_NAME,
            version=1,
            stages=[
                TemplateStage(
                    code="WITH_BID",
                    label="With bid",
                    help_text="Customer expects the document or commitment with the quotation.",
                ),
                TemplateStage(
                    code="AFTER_AWARD",
                    label="After anticipated award",
                    help_text="Timing is calculated from the anticipated award or PO date.",
                ),
                TemplateStage(
                    code="BEFORE_DELIVERY",
                    label="Before forecast delivery",
                    help_text="Timing is calculated back from forecast delivery where relevant.",
                ),
                TemplateStage(
                    code="WITH_DELIVERY",
                    label="With delivery",
                    help_text=(
                        "Commitment is handed over; ContractIQ does not track execution submission."
                    ),
                ),
            ],
            provenance=Provenance(
                created_by=Actor.SYSTEM,
                agent_name="ContractIQ",
                source_location="OPS-05B generic bid-stage VDRL template",
                created_at=now,
                human_confirmed=False,
            ),
            created_at=now,
        )
        return self.repository.ensure_template(template)

    def create_package(
        self, command: SupplierPackageCreate | Mapping[str, Any], actor: str
    ) -> SupplierPackage:
        who = self._actor(actor)
        value = (
            command
            if isinstance(command, SupplierPackageCreate)
            else SupplierPackageCreate.model_validate(command)
        )
        bid = self.bid_repository.get_bid(value.bid_id)
        if bid is None:
            raise ValueError("Selected Bid does not exist")
        template = self.ensure_standard_template()
        now = self._now()
        package = SupplierPackage(
            **value.model_dump(),
            package_id=self._id("VPK"),
            template_id=template.template_id,
            template_version=template.version,
            version_token=self._id("VT"),
            provenance=self._provenance(who, value.source_vdrl_reference or "Manual package setup"),
            created_at=now,
            updated_at=now,
        )
        return self.repository.create_package(
            package,
            self._audit(
                package.bid_id,
                who,
                "vendor_vdrl_package_created",
                {"package_id": package.package_id, "package_code": package.package_code},
            ),
        )

    def package(self, package_id: str) -> SupplierPackage:
        value = self.repository.get_package(package_id)
        if value is None:
            raise VendorDocumentNotFoundError("supplier/equipment package not found")
        return value

    def _new_requirement(
        self,
        value: CustomerRequirementCreate,
        actor: str,
        source: str,
    ) -> CustomerRequirement:
        package = self.package(value.package_id)
        original_date = value.original_contractual_date or calculate_requested_date(
            package, value.timing_anchor, value.timing_offset_days
        )
        now = self._now()
        return CustomerRequirement(
            **value.model_dump(exclude={"original_contractual_date"}),
            original_contractual_date=original_date,
            requirement_id=self._id("VREQ"),
            verification_status=VerificationStatus.NOT_REVIEWED,
            proposed_manufacturer=package.proposed_manufacturer,
            internal_owner=package.internal_owner,
            commercial_impact=CommercialImpact.NONE,
            bid_disposition=BidDisposition.NONE,
            version=1,
            provenance=self._provenance(actor, source),
            created_at=now,
            updated_at=now,
        )

    def create_requirement(
        self, command: CustomerRequirementCreate | Mapping[str, Any], actor: str
    ) -> CustomerRequirement:
        who = self._actor(actor)
        value = (
            command
            if isinstance(command, CustomerRequirementCreate)
            else CustomerRequirementCreate.model_validate(command)
        )
        requirement = self._new_requirement(
            value, who, value.source_row_reference or "Manual VDRL entry"
        )
        package = self.package(requirement.package_id)
        return self.repository.create_requirement(
            requirement,
            self._audit(
                package.bid_id,
                who,
                "vendor_vdrl_requirement_created",
                {
                    "package_id": package.package_id,
                    "requirement_id": requirement.requirement_id,
                    "customer_requirement_code": requirement.customer_requirement_code,
                },
            ),
        )

    def update_verification(
        self,
        requirement_id: str,
        command: RequirementVerificationUpdate | Mapping[str, Any],
        actor: str,
    ) -> CustomerRequirement:
        who = self._actor(actor)
        current = self.repository.get_requirement(requirement_id)
        if current is None:
            raise VendorDocumentNotFoundError("customer requirement not found")
        value = (
            command
            if isinstance(command, RequirementVerificationUpdate)
            else RequirementVerificationUpdate.model_validate(command)
        )
        verification = ManufacturerVerification.model_validate(
            value.model_dump(exclude={"expected_version"})
        )
        package = self.package(current.package_id)
        return self.repository.update_verification(
            requirement_id,
            value.expected_version,
            verification,
            self._now(),
            self._audit(
                package.bid_id,
                who,
                "vendor_vdrl_verification_updated",
                {
                    "package_id": package.package_id,
                    "requirement_id": requirement_id,
                    "before_status": current.verification_status.value,
                    "after_status": verification.verification_status.value,
                },
            ),
        )

    def bulk_update(self, package_id: str, command: BulkVerificationUpdate, actor: str) -> None:
        who = self._actor(actor)
        package = self.package(package_id)
        current = {
            item.requirement_id: item for item in self.repository.list_requirements(package_id)
        }
        values: dict[str, Any] = {}
        if command.proposed_manufacturer is not None:
            values["proposed_manufacturer"] = command.proposed_manufacturer
        if command.internal_owner is not None:
            values["internal_owner"] = command.internal_owner
        if command.verification_status is not None:
            values["verification_status"] = command.verification_status.value
        for target in command.targets:
            item = current.get(target.requirement_id)
            if item is None:
                raise ValueError("selected requirement does not belong to this package")
            candidate = item.model_copy(
                update={
                    key: command.verification_status if key == "verification_status" else value
                    for key, value in values.items()
                }
            )
            ManufacturerVerification.model_validate(
                {key: getattr(candidate, key) for key in ManufacturerVerification.model_fields}
            )
        self.repository.bulk_update(
            command,
            values,
            self._now(),
            self._audit(
                package.bid_id,
                who,
                "vendor_vdrl_bulk_updated",
                {
                    "package_id": package_id,
                    "requirement_ids": [target.requirement_id for target in command.targets],
                    "fields": sorted(values),
                },
            ),
        )

    @staticmethod
    def _boolean(value: str, field: str) -> bool:
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "required"}:
            return True
        if normalized in {"0", "false", "no", "n", "not required"}:
            return False
        raise ValueError(f"{field} must be yes or no")

    def preview_import(self, package_id: str, csv_text: str) -> ImportPreview:
        self.package(package_id)
        reader = csv.DictReader(io.StringIO(csv_text))
        required_headers = {"requirement_code", "title", "required"}
        errors: list[str] = []
        if reader.fieldnames is None:
            return ImportPreview(
                rows=[],
                declared_required_total=None,
                actual_required_total=0,
                errors=["CSV header is required"],
            )
        missing = sorted(required_headers - set(reader.fieldnames))
        if missing:
            errors.append(f"Missing required column(s): {', '.join(missing)}")
        results: list[ImportRowResult] = []
        seen: set[str] = set()
        declared_values: set[int] = set()
        actual_required = 0
        for row_number, raw in enumerate(reader, start=2):
            values = {key: (value or "").strip() for key, value in raw.items() if key is not None}
            row_errors: list[str] = []
            code = values.get("requirement_code", "").upper()
            if not code:
                row_errors.append("requirement_code is required")
            elif code in seen:
                row_errors.append(f"duplicate requirement code {code}")
            seen.add(code)
            if not values.get("title"):
                row_errors.append("title is required")
            try:
                marked = self._boolean(values.get("required", ""), "required")
                actual_required += int(marked)
            except ValueError as exc:
                row_errors.append(str(exc))
            declared = values.get("declared_required_total", "")
            if declared:
                try:
                    declared_values.add(int(declared))
                except ValueError:
                    row_errors.append("declared_required_total must be an integer")
            anchor = values.get("timing_anchor", "OTHER") or "OTHER"
            if anchor not in {item.value for item in TimingAnchor}:
                row_errors.append(f"unknown timing anchor {anchor}")
            try:
                int(values.get("offset_days", "0") or "0")
            except ValueError:
                row_errors.append("offset_days must be an integer")
            if values.get("original_contractual_date"):
                try:
                    date.fromisoformat(values["original_contractual_date"])
                except ValueError:
                    row_errors.append("original_contractual_date must use YYYY-MM-DD")
            results.append(ImportRowResult(row_number=row_number, values=values, errors=row_errors))
        declared_total = next(iter(declared_values)) if len(declared_values) == 1 else None
        if len(declared_values) > 1:
            errors.append("Rows contain conflicting declared required totals")
        if declared_total is not None and declared_total != actual_required:
            errors.append(
                f"Declared required total {declared_total} does not match "
                f"{actual_required} marked-required rows"
            )
        if not results:
            errors.append("CSV contains no requirement rows")
        return ImportPreview(
            rows=results,
            declared_required_total=declared_total,
            actual_required_total=actual_required,
            errors=errors,
        )

    @staticmethod
    def import_digest(csv_text: str) -> str:
        return hashlib.sha256(csv_text.encode()).hexdigest()

    def confirm_import(
        self,
        package_id: str,
        csv_text: str,
        expected_digest: str,
        actor: str,
    ) -> list[CustomerRequirement]:
        who = self._actor(actor)
        if not expected_digest or self.import_digest(csv_text) != expected_digest:
            raise ValueError("CSV content changed after preview; preview it again")
        preview = self.preview_import(package_id, csv_text)
        if not preview.valid:
            raise ValueError("CSV has validation errors and cannot be imported")
        existing = {
            item.customer_requirement_code.upper()
            for item in self.repository.list_requirements(package_id)
        }
        created: list[CustomerRequirement] = []
        for row in preview.rows:
            values = row.values
            code = values["requirement_code"].upper()
            if code in existing:
                raise ValueError(f"requirement code already exists in package: {code}")
            existing.add(code)
            command = CustomerRequirementCreate.model_validate(
                {
                    "package_id": package_id,
                    "customer_requirement_code": code,
                    "deliverable_title": values["title"],
                    "description": values.get("description", ""),
                    "required": self._boolean(values["required"], "required"),
                    "requested_stages": [
                        stage.strip()
                        for stage in values.get("requested_stages", "").split("|")
                        if stage.strip()
                    ],
                    "timing_anchor": values.get("timing_anchor") or "OTHER",
                    "timing_offset_days": int(values.get("offset_days") or 0),
                    "original_contractual_timing": values.get("original_contractual_timing"),
                    "original_contractual_date": values.get("original_contractual_date") or None,
                    "customer_notes": values.get("customer_notes"),
                    "source_row_reference": values.get("source_row_reference")
                    or f"CSV row {row.row_number}",
                    "source_revision": values.get("source_revision"),
                    "applicable": self._boolean(values.get("applicable", "yes"), "applicable"),
                }
            )
            created.append(self._new_requirement(command, who, f"CSV row {row.row_number}"))
        package = self.package(package_id)
        return self.repository.import_requirements(
            created,
            self._audit(
                package.bid_id,
                who,
                "vendor_vdrl_requirements_imported",
                {
                    "package_id": package_id,
                    "row_count": len(created),
                    "csv_sha256": expected_digest,
                },
            ),
        )

    def readiness(self, package_id: str) -> VdrlReadiness:
        self.package(package_id)
        return readiness_for(self.repository.list_requirements(package_id))

    def register_rows(
        self,
        *,
        bid_id: str | None = None,
        package_id: str | None = None,
        requirement_code: str | None = None,
        stage: str | None = None,
        verification_status: VerificationStatus | None = None,
        manufacturer: str | None = None,
        owner: str | None = None,
        commercial_impact: CommercialImpact | None = None,
        attention: bool | None = None,
    ) -> list[dict[str, Any]]:
        packages = {item.package_id: item for item in self.repository.list_packages(bid_id)}
        rows: list[dict[str, Any]] = []
        for item in self.repository.list_requirements(package_id):
            package = packages.get(item.package_id)
            if package is None:
                continue
            blockers = [
                blocker
                for blocker in readiness_for([item]).blockers
                if blocker.requirement_id == item.requirement_id
            ]
            if (
                requirement_code
                and requirement_code.lower() not in item.customer_requirement_code.lower()
            ):
                continue
            if stage and stage.upper() not in item.requested_stages:
                continue
            if verification_status and item.verification_status is not verification_status:
                continue
            if (
                manufacturer
                and manufacturer.lower() not in (item.proposed_manufacturer or "").lower()
            ):
                continue
            if owner and owner.lower() not in (item.internal_owner or "").lower():
                continue
            if commercial_impact and item.commercial_impact is not commercial_impact:
                continue
            if attention is not None and bool(blockers) is not attention:
                continue
            bid = self.bid_repository.get_bid(package.bid_id)
            rows.append(
                {
                    "requirement": item,
                    "package": package,
                    "bid": bid,
                    "requested_date": item.original_contractual_date
                    or calculate_requested_date(
                        package, item.timing_anchor, item.timing_offset_days
                    ),
                    "blockers": blockers,
                }
            )
        status_rank = {
            VerificationStatus.NOT_REVIEWED: 0,
            VerificationStatus.AWAITING_MANUFACTURER: 1,
            VerificationStatus.CLARIFICATION_REQUIRED: 2,
            VerificationStatus.CANNOT_COMPLY: 3,
            VerificationStatus.CONFIRMED_WITH_EXCEPTION: 4,
            VerificationStatus.CONFIRMED_COMPLIANT: 5,
            VerificationStatus.NOT_APPLICABLE: 6,
        }
        rows.sort(
            key=lambda row: (
                not bool(row["blockers"]),
                status_rank[row["requirement"].verification_status],
                row["requirement"].customer_requirement_code,
                row["requirement"].requirement_id,
            )
        )
        return rows

    def handover_csv(self, package_id: str) -> str:
        package = self.package(package_id)
        bid = self.bid_repository.get_bid(package.bid_id)
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(csv_safe_row(["Vendor Document Handover"]))
        writer.writerow(
            csv_safe_row(["Bid", f"{bid.bid_id} — {bid.project_name}" if bid else package.bid_id])
        )
        writer.writerow(
            csv_safe_row(["Package", f"{package.package_code} — {package.package_name}"])
        )
        writer.writerow(csv_safe_row(["Customer/EPCM", package.customer_epcm or ""]))
        writer.writerow(csv_safe_row(["Manufacturer", package.proposed_manufacturer]))
        writer.writerow(
            csv_safe_row(
                ["Source VDRL", package.source_vdrl_reference or "", package.source_revision or ""]
            )
        )
        assessments = {
            item.requirement_id: assess_manufacturer_handover(
                required=item.required,
                applicable=item.applicable,
                verification_status=item.verification_status,
                response_source=item.response_source,
                response_received_date=item.response_received_date,
                internal_owner=item.internal_owner,
                commercial_impact=item.commercial_impact,
                bid_disposition=item.bid_disposition,
                disposition_approved=item.disposition_approved,
                unresolved_action=item.unresolved_action,
            )
            for item in self.repository.list_requirements(package_id)
        }
        shared_ready = bool(assessments) and all(item.ready for item in assessments.values())
        shared_blockers = tuple(
            dict.fromkeys(
                reason for item in assessments.values() for reason in item.blocking_reasons
            )
        )
        writer.writerow(["Overall handover readiness", "Ready" if shared_ready else "Not ready"])
        writer.writerow(["Blocking reasons", " | ".join(shared_blockers)])
        writer.writerow([])
        writer.writerow(
            [
                "Requirement code",
                "Customer requirement",
                "Description",
                "Requested stages",
                "Requested timing",
                "Manufacturer verification",
                "Manufacturer response status",
                "Response evidence status",
                "Internal accountability status",
                "Commercial and disposition status",
                "Handover readiness",
                "Handover blocking reasons",
                "Manufacturer response source",
                "Manufacturer response date",
                "Manufacturer notes",
                "Manufacturer commitment",
                "Evidence/reference",
                "Bid exception",
                "Commercial impact",
                "Bid disposition",
                "Unresolved action",
                "Owner",
                "Handover note",
            ]
        )
        for item in self.repository.list_requirements(package_id):
            assessment = assessments[item.requirement_id]
            writer.writerow(
                csv_safe_row(
                    [
                        item.customer_requirement_code,
                        item.deliverable_title,
                        item.description,
                        " | ".join(item.requested_stages),
                        item.original_contractual_timing or item.original_contractual_date or "",
                        item.verification_status.value,
                        assessment.response_status,
                        assessment.evidence_status,
                        assessment.accountability_status,
                        assessment.commercial_disposition_status,
                        assessment.readiness_label,
                        " | ".join(assessment.blocking_reasons),
                        item.response_source or "",
                        item.response_received_date or "",
                        item.manufacturer_notes or "",
                        " | ".join(item.committed_stages)
                        + (f"; {item.committed_timing}" if item.committed_timing else ""),
                        item.evidence_reference or "",
                        item.proposed_exception or "",
                        item.commercial_impact.value,
                        item.bid_disposition.value
                        + (" (approved)" if item.disposition_approved else ""),
                        item.unresolved_action or "",
                        item.internal_owner or "",
                        item.handover_note or "",
                    ]
                )
            )
        return output.getvalue()


__all__ = [
    "STANDARD_TEMPLATE_ID",
    "StaleVendorDocumentError",
    "VendorDocumentNotFoundError",
    "VendorDocumentService",
    "ReadinessAttention",
]
