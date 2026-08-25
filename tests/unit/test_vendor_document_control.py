from __future__ import annotations

import socket
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from core.bid_repository import BidRepository
from core.database import Database
from core.schemas import Bid
from core.vendor_document_control import (
    BidDisposition,
    BulkRequirementTarget,
    BulkVerificationUpdate,
    CommercialImpact,
    CustomerRequirementCreate,
    RequirementVerificationUpdate,
    SupplierPackageCreate,
    TimingAnchor,
    VerificationStatus,
    calculate_requested_date,
)
from core.vendor_document_repository import (
    VENDOR_DOCUMENT_MIGRATION_ID,
    StaleVendorDocumentError,
    VendorDocumentRepository,
)
from core.vendor_document_service import VendorDocumentService


def _bid() -> Bid:
    return Bid.model_validate(
        {
            "bid_id": "B-2026-0042",
            "customer": "Northline Mining",
            "customer_type": "epcm",
            "project_name": "Northline Expansion Bid",
            "sales_owner": "Alex",
            "bc_owner": "Jason",
            "release_date": "2026-08-20",
            "customer_due_date": "2026-09-30",
            "internal_due_date": "2026-09-25",
            "anticipated_award_date": "2026-11-15",
            "estimated_value": "2500000",
            "classification": "level_3",
            "created_at": "2026-08-20T09:00:00+00:00",
            "updated_at": "2026-08-20T09:00:00+00:00",
        }
    )


def _service(
    tmp_path: Path,
) -> tuple[VendorDocumentService, VendorDocumentRepository, BidRepository, Database]:
    db = Database(tmp_path / "ops05b.db")
    bids = BidRepository(db)
    bids.create_bid(_bid())
    repository = VendorDocumentRepository(db)
    ids = iter(UUID(int=value) for value in range(1, 10000))
    service = VendorDocumentService(
        repository,
        bids,
        now_factory=lambda: datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        id_factory=lambda: next(ids),
    )
    service.ensure_standard_template()
    return service, repository, bids, db


def _package(service: VendorDocumentService) -> str:
    return service.create_package(
        SupplierPackageCreate(
            bid_id=_bid().bid_id,
            package_name="Low-voltage motor control centre",
            package_code="LV-MCC-01",
            customer_epcm="Example EPCM",
            proposed_manufacturer="Atlas Switchgear",
            manufacturer_contact="Morgan Lee",
            internal_owner="Taylor Engineer",
            source_vdrl_reference="VDRL-ELE-001",
            source_revision="A",
            anticipated_award_date=date(2026, 11, 15),
            forecast_delivery_date=date(2027, 6, 1),
            notes="Bid-stage verification only",
        ),
        "Jason",
    ).package_id


def _requirement(
    service: VendorDocumentService,
    package_id: str,
    *,
    code: str = "A01",
    title: str = "General arrangement drawing",
) -> str:
    return service.create_requirement(
        CustomerRequirementCreate(
            package_id=package_id,
            customer_requirement_code=code,
            deliverable_title=title,
            description="Customer requires the proposed equipment arrangement.",
            requested_stages=["WITH_BID", "AFTER_AWARD"],
            timing_anchor=TimingAnchor.ANTICIPATED_AWARD,
            timing_offset_days=28,
            original_contractual_timing="ARA +4 weeks",
            customer_notes="Confirm drawing can be supplied.",
            source_row_reference="VDRL row 12",
            source_revision="A",
        ),
        "Jason",
    ).requirement_id


def _verification(
    version: int,
    *,
    status: VerificationStatus = VerificationStatus.CONFIRMED_COMPLIANT,
    **updates: object,
) -> RequirementVerificationUpdate:
    values: dict[str, object] = {
        "expected_version": version,
        "verification_status": status,
        "proposed_manufacturer": "Atlas Switchgear",
        "response_source": "Email from Morgan Lee dated 2026-08-21",
        "internal_owner": "Taylor Engineer",
        "confirmation_requested_date": "2026-08-20",
        "response_received_date": "2026-08-21",
        "committed_stages": ["WITH_BID", "AFTER_AWARD"],
        "committed_timing": "With bid and 4 weeks after award",
        "evidence_reference": "Controlled email reference EMAIL-42",
        "commercial_impact": "NONE",
        "bid_disposition": "NONE",
        "handover_note": "Execution owner to confirm drawing numbering.",
    }
    values.update(updates)
    return RequirementVerificationUpdate.model_validate(values)


def test_package_requires_authoritative_bid_and_uses_generic_template(tmp_path: Path) -> None:
    service, repository, _bids, _db = _service(tmp_path)
    package_id = _package(service)
    package = repository.get_package(package_id)
    assert package is not None
    assert package.bid_id == "B-2026-0042"
    assert package.template_id == "VDRL-SYSTEM-GENERIC-BID-V1"
    with pytest.raises(ValueError, match="Selected Bid does not exist"):
        service.create_package(
            {
                "bid_id": "B-2026-9999",
                "package_name": "Invalid",
                "package_code": "BAD",
                "proposed_manufacturer": "Supplier",
                "internal_owner": "Owner",
            },
            "Jason",
        )
    assert len(repository.list_packages()) == 1


def test_original_customer_requirement_is_preserved_from_response_updates(
    tmp_path: Path,
) -> None:
    service, repository, _bids, db = _service(tmp_path)
    requirement_id = _requirement(service, _package(service))
    before = repository.get_requirement(requirement_id)
    assert before is not None
    updated = service.update_verification(requirement_id, _verification(before.version), "Jason")
    assert updated.customer_requirement_code == before.customer_requirement_code
    assert updated.deliverable_title == before.deliverable_title
    assert updated.description == before.description
    assert updated.requested_stages == before.requested_stages
    assert updated.original_contractual_timing == "ARA +4 weeks"
    with (
        db._conn() as conn,
        pytest.raises(
            sqlite3.IntegrityError, match="original customer VDRL requirement is immutable"
        ),
    ):
        conn.execute(
            "UPDATE vendor_bid_requirements SET deliverable_title='Overwritten' "
            "WHERE requirement_id=?",
            (requirement_id,),
        )


@pytest.mark.parametrize(
    ("status", "updates", "message"),
    [
        (
            VerificationStatus.CONFIRMED_WITH_EXCEPTION,
            {"proposed_exception": None},
            "requires a proposed modification",
        ),
        (
            VerificationStatus.CANNOT_COMPLY,
            {"manufacturer_notes": None, "proposed_exception": None},
            "requires a reason",
        ),
        (
            VerificationStatus.CLARIFICATION_REQUIRED,
            {"response_received_date": None, "unresolved_action": None},
            "needs a question or action",
        ),
        (
            VerificationStatus.NOT_APPLICABLE,
            {"manufacturer_notes": None},
            "requires a reason",
        ),
    ],
)
def test_status_dependent_verification_validation(
    status: VerificationStatus, updates: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _verification(1, status=status, **updates)


def test_response_status_requires_response_date() -> None:
    with pytest.raises(ValidationError, match="requires response received date"):
        _verification(1, response_received_date=None)


def test_deterministic_readiness_explains_each_blocking_reason(tmp_path: Path) -> None:
    service, repository, _bids, _db = _service(tmp_path)
    package_id = _package(service)
    requirement_id = _requirement(service, package_id)
    initial = service.readiness(package_id)
    assert initial.ready is False
    assert initial.counts.not_reviewed == 1
    assert {item.code.value for item in initial.blockers} == {"NOT_REVIEWED"}
    current = repository.get_requirement(requirement_id)
    assert current is not None
    service.update_verification(
        requirement_id,
        _verification(
            current.version,
            status=VerificationStatus.CONFIRMED_WITH_EXCEPTION,
            proposed_exception="Certified issue will be four weeks after award",
            commercial_impact=CommercialImpact.UNKNOWN,
            bid_disposition=BidDisposition.QUALIFIED,
            disposition_approved=False,
        ),
        "Jason",
    )
    blocked = service.readiness(package_id)
    assert {item.code.value for item in blocked.blockers} == {
        "EXCEPTION_UNRESOLVED",
        "UNKNOWN_COMMERCIAL_IMPACT",
    }
    current = repository.get_requirement(requirement_id)
    assert current is not None
    service.update_verification(
        requirement_id,
        _verification(
            current.version,
            status=VerificationStatus.CONFIRMED_WITH_EXCEPTION,
            proposed_exception="Certified issue will be four weeks after award",
            commercial_impact=CommercialImpact.INCLUDED,
            bid_disposition=BidDisposition.QUALIFIED,
            disposition_approved=True,
        ),
        "Jason",
    )
    ready = service.readiness(package_id)
    assert ready.ready is True
    assert ready.counts.ready_for_handover == 1


def test_cannot_comply_and_clarification_prevent_readiness(tmp_path: Path) -> None:
    service, repository, _bids, _db = _service(tmp_path)
    package_id = _package(service)
    cannot_id = _requirement(service, package_id, code="A01")
    clarification_id = _requirement(service, package_id, code="A02", title="Heat run test")
    cannot = repository.get_requirement(cannot_id)
    clarification = repository.get_requirement(clarification_id)
    assert cannot is not None and clarification is not None
    service.update_verification(
        cannot_id,
        _verification(
            cannot.version,
            status=VerificationStatus.CANNOT_COMPLY,
            manufacturer_notes="Test facility is unavailable",
            bid_disposition=BidDisposition.EXCLUDED,
            disposition_approved=False,
            commercial_impact=CommercialImpact.EXCLUDED,
        ),
        "Jason",
    )
    service.update_verification(
        clarification_id,
        _verification(
            clarification.version,
            status=VerificationStatus.CLARIFICATION_REQUIRED,
            response_received_date=None,
            unresolved_action="Ask EPCM whether a type-test certificate is acceptable",
        ),
        "Jason",
    )
    blockers = {item.code.value for item in service.readiness(package_id).blockers}
    assert "CANNOT_COMPLY_UNRESOLVED" in blockers
    assert "CLARIFICATION_REQUIRED" in blockers


def test_bulk_assignment_is_atomic_on_stale_target(tmp_path: Path) -> None:
    service, repository, bids, _db = _service(tmp_path)
    package_id = _package(service)
    first_id = _requirement(service, package_id, code="A01")
    second_id = _requirement(service, package_id, code="A02", title="Data sheet")
    first = repository.get_requirement(first_id)
    second = repository.get_requirement(second_id)
    assert first is not None and second is not None
    audits_before = len(bids.list_audit(bid_id=_bid().bid_id))
    with pytest.raises(StaleVendorDocumentError):
        service.bulk_update(
            package_id,
            BulkVerificationUpdate(
                targets=[
                    BulkRequirementTarget(
                        requirement_id=first.requirement_id,
                        expected_version=first.version,
                    ),
                    BulkRequirementTarget(
                        requirement_id=second.requirement_id,
                        expected_version=second.version + 1,
                    ),
                ],
                internal_owner="New owner",
                verification_status=VerificationStatus.AWAITING_MANUFACTURER,
            ),
            "Jason",
        )
    assert repository.get_requirement(first_id) == first
    assert repository.get_requirement(second_id) == second
    assert len(bids.list_audit(bid_id=_bid().bid_id)) == audits_before


def test_csv_preview_detects_33_declared_versus_37_marked(tmp_path: Path) -> None:
    service, _repository, _bids, _db = _service(tmp_path)
    package_id = _package(service)
    rows = ["requirement_code,title,required,declared_required_total"]
    rows.extend(f"R{index:02d},Requirement {index},yes,33" for index in range(1, 38))
    csv_text = "\n".join(rows)
    preview = service.preview_import(package_id, csv_text)
    assert preview.valid is False
    assert preview.actual_required_total == 37
    assert preview.declared_required_total == 33
    assert "does not match 37 marked-required rows" in preview.errors[0]


def test_csv_import_preserves_client_values_and_is_atomic(tmp_path: Path) -> None:
    service, repository, bids, _db = _service(tmp_path)
    package_id = _package(service)
    csv_text = (
        "requirement_code,title,required,requested_stages,timing_anchor,offset_days,"
        "original_contractual_timing,customer_notes,source_row_reference,source_revision,applicable\n"
        "D01,Motor data sheet,yes,WITH_BID|AFTER_AWARD,ANTICIPATED_AWARD,14,"
        "ARA +2 weeks,Use customer title exactly,Row 18,B,yes\n"
    )
    created = service.confirm_import(package_id, csv_text, service.import_digest(csv_text), "Jason")
    assert len(created) == 1
    stored = repository.get_requirement(created[0].requirement_id)
    assert stored is not None
    assert stored.deliverable_title == "Motor data sheet"
    assert stored.requested_stages == ["WITH_BID", "AFTER_AWARD"]
    assert stored.original_contractual_timing == "ARA +2 weeks"
    assert stored.customer_notes == "Use customer title exactly"
    assert stored.source_row_reference == "Row 18"
    assert stored.source_revision == "B"
    before_count = len(repository.list_requirements(package_id))
    audits_before = len(bids.list_audit(bid_id=_bid().bid_id))
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        service.confirm_import(package_id, csv_text, service.import_digest(csv_text), "Jason")
    assert len(repository.list_requirements(package_id)) == before_count
    assert len(bids.list_audit(bid_id=_bid().bid_id)) == audits_before


def test_register_combined_filters_use_intersection_and_unresolved_first(
    tmp_path: Path,
) -> None:
    service, repository, _bids, _db = _service(tmp_path)
    package_id = _package(service)
    first_id = _requirement(service, package_id, code="A01")
    _requirement(service, package_id, code="B02", title="Manual")
    first = repository.get_requirement(first_id)
    assert first is not None
    service.update_verification(first_id, _verification(first.version), "Jason")
    rows = service.register_rows(
        bid_id=_bid().bid_id,
        package_id=package_id,
        requirement_code="B02",
        stage="WITH_BID",
        verification_status=VerificationStatus.NOT_REVIEWED,
        manufacturer="Atlas",
        owner="Taylor",
        commercial_impact=CommercialImpact.NONE,
        attention=True,
    )
    assert [row["requirement"].customer_requirement_code for row in rows] == ["B02"]
    all_rows = service.register_rows(package_id=package_id)
    assert all_rows[0]["requirement"].customer_requirement_code == "B02"


def test_handover_contains_original_commitment_exception_and_unresolved_action(
    tmp_path: Path,
) -> None:
    service, repository, _bids, _db = _service(tmp_path)
    package_id = _package(service)
    requirement_id = _requirement(service, package_id)
    requirement = repository.get_requirement(requirement_id)
    assert requirement is not None
    service.update_verification(
        requirement_id,
        _verification(
            requirement.version,
            status=VerificationStatus.CONFIRMED_WITH_EXCEPTION,
            proposed_exception="Issue certified drawing 28 days after award",
            commercial_impact=CommercialImpact.INCLUDED,
            bid_disposition=BidDisposition.QUALIFIED,
            disposition_approved=True,
            unresolved_action="Execution owner to confirm customer numbering",
        ),
        "Jason",
    )
    handover = service.handover_csv(package_id)
    assert "B-2026-0042 — Northline Expansion Bid" in handover
    assert "General arrangement drawing" in handover
    assert "WITH_BID | AFTER_AWARD" in handover
    assert "Email from Morgan Lee dated 2026-08-21" in handover
    assert "2026-08-21" in handover
    assert "With bid and 4 weeks after award" in handover
    assert "Issue certified drawing 28 days after award" in handover
    assert "INCLUDED" in handover
    assert "QUALIFIED (approved)" in handover
    assert "Execution owner to confirm customer numbering" in handover


def test_requested_date_is_deterministic_and_not_execution_actual(tmp_path: Path) -> None:
    service, _repository, _bids, _db = _service(tmp_path)
    package = service.package(_package(service))
    assert calculate_requested_date(package, TimingAnchor.ANTICIPATED_AWARD, 28) == date(
        2026, 12, 13
    )
    assert calculate_requested_date(package, TimingAnchor.FORECAST_DELIVERY, -14) == date(
        2027, 5, 18
    )


def test_unpublished_migration_is_additive_idempotent_and_integral(tmp_path: Path) -> None:
    db = Database(tmp_path / "migration.db")
    bids = BidRepository(db)
    bids.create_bid(_bid())
    with db._conn() as conn:
        conn.execute("CREATE TABLE production_sentinel(id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO production_sentinel VALUES('KEEP','unchanged')")
        conn.commit()
    first = VendorDocumentRepository(db)
    second = VendorDocumentRepository(db)
    assert isinstance(first, VendorDocumentRepository)
    assert isinstance(second, VendorDocumentRepository)
    with db._conn() as conn:
        markers = [
            row[0]
            for row in conn.execute(
                "SELECT migration_id FROM vendor_document_schema_migrations"
            ).fetchall()
        ]
        assert markers == [VENDOR_DOCUMENT_MIGRATION_ID]
        assert conn.execute("SELECT value FROM production_sentinel").fetchone()[0] == "unchanged"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'vendor_%'"
            ).fetchall()
        }
    assert "vendor_document_submissions" not in tables
    assert "vendor_documents" not in tables
    assert tables == {
        "vendor_bid_packages",
        "vendor_bid_requirements",
        "vendor_document_schema_migrations",
        "vendor_vdrl_templates",
    }


def test_vendor_control_has_no_network_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "create_connection", blocked)
    service, _repository, _bids, _db = _service(tmp_path)
    package_id = _package(service)
    _requirement(service, package_id)
    assert service.readiness(package_id).ready is False
