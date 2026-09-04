"""Focused OPS-07 migration, concurrency, relationship, and export tests."""

import csv
import io
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from core.bid_repository import BidRepository
from core.database import Database
from core.document_repository import DocumentRepository
from core.export_controls import csv_safe_cell, manufacturer_confirmation_clear
from core.gate_service import _manufacturer_coverage_clear
from core.ops07 import BulkRequirementTarget, BulkResponsibilityAssignment, Ops07Repository
from core.ops07w import Ops07WorkflowRepository
from core.requirement_repository import RequirementRepository, StaleRequirementError
from core.requirement_service import RequirementService
from core.scope_repository import ScopeInterfaceRepository
from core.vendor_document_control import (
    CustomerRequirementCreate,
    RequirementVerificationUpdate,
    SupplierPackageCreate,
    VerificationStatus,
)
from core.vendor_document_repository import VendorDocumentRepository
from core.vendor_document_service import VendorDocumentService
from core.work_item_repository import WorkItemRepository
from core.work_item_service import WorkItemService


def _stack(path: Path, valid_bid):
    db = Database(path)
    bids = BidRepository(db)
    bids.create_bid(valid_bid)
    WorkItemRepository(db)
    documents = DocumentRepository(db)
    requirements = RequirementRepository(db)
    ScopeInterfaceRepository(db)
    VendorDocumentRepository(db)
    ops = Ops07Repository(db)
    service = RequirementService(requirements, bids, documents)
    return db, requirements, service, ops


def _create(service: RequirementService, bid_id: str, title: str):
    return service.create_requirement(
        {
            "bid_id": bid_id,
            "title": title,
            "statement": f"{title} statement",
            "origin": "INTERNAL",
            "category": "TECHNICAL",
            "significance": "MANDATORY",
            "contributor": "Engineering",
        },
        "Jason",
    )


def test_contributor_migration_is_nullable_restart_safe_and_not_backfilled(tmp_path, valid_bid):
    path = tmp_path / "ops07.db"
    db, repository, service, _ = _stack(path, valid_bid)
    created = _create(service, valid_bid.bid_id, "New")
    assert created.contributor == "Engineering"
    with db._conn() as conn:
        conn.execute("DROP INDEX idx_requirements_contributor")
        conn.execute("ALTER TABLE requirements DROP COLUMN contributor")
        before = conn.execute(
            "SELECT version FROM requirements WHERE requirement_id=?", (created.requirement_id,)
        ).fetchone()
        assert before is not None and before["version"] == 1
    RequirementRepository(db)
    Ops07Repository(db)
    loaded = repository.get(created.requirement_id)
    assert loaded is not None and loaded.contributor is None
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_bulk_assignment_is_atomic_audited_and_stale_rolls_back(tmp_path, valid_bid):
    _, repository, service, ops = _stack(tmp_path / "bulk.db", valid_bid)
    first = _create(service, valid_bid.bid_id, "A")
    second = _create(service, valid_bid.bid_id, "B")
    ops.bulk_assign(
        valid_bid.bid_id,
        BulkResponsibilityAssignment(
            targets=[
                BulkRequirementTarget(requirement_id=first.requirement_id, expected_version=1),
                BulkRequirementTarget(requirement_id=second.requirement_id, expected_version=1),
            ],
            owner="Jason",
            contributor="Engineering",
            reviewer="Legal",
        ),
        "Jason",
    )
    loaded = repository.get(first.requirement_id)
    assert loaded is not None and loaded.reviewer == "Legal"
    with pytest.raises(StaleRequirementError):
        ops.bulk_assign(
            valid_bid.bid_id,
            BulkResponsibilityAssignment(
                targets=[
                    BulkRequirementTarget(requirement_id=first.requirement_id, expected_version=2),
                    BulkRequirementTarget(requirement_id=second.requirement_id, expected_version=1),
                ],
                owner="Changed",
            ),
            "Jason",
        )
    loaded = repository.get(first.requirement_id)
    assert loaded is not None and loaded.owner == "Jason"


def test_my_work_relationship_is_fk_backed_bid_scoped_and_unique(tmp_path, valid_bid):
    db, _repository, service, ops = _stack(tmp_path / "work.db", valid_bid)
    requirement = _create(service, valid_bid.bid_id, "Work source")
    work_service = WorkItemService(WorkItemRepository(db), BidRepository(db))
    work = work_service.create_work_item(
        {"bid_id": valid_bid.bid_id, "title": "Close requirement"}, "Jason"
    )
    ops.link_work_item(
        bid_id=valid_bid.bid_id,
        work_item_id=work.work_item_id,
        source_kind="requirement",
        source_id=requirement.requirement_id,
        purpose="closure",
        actor="Jason",
    )
    assert ops.coverage(requirement.requirement_id)["work"][0]["work_item_id"] == work.work_item_id
    with pytest.raises(ValueError, match="already linked"):
        ops.link_work_item(
            bid_id=valid_bid.bid_id,
            work_item_id=work.work_item_id,
            source_kind="requirement",
            source_id=requirement.requirement_id,
            purpose="closure",
            actor="Jason",
        )
    with pytest.raises(ValueError, match="source kind"):
        ops.link_work_item(
            bid_id=valid_bid.bid_id,
            work_item_id=work.work_item_id,
            source_kind="free-form",
            source_id=requirement.requirement_id,
            purpose="other",
            actor="Jason",
        )


def test_handover_is_deterministic_and_silence_unresolved(tmp_path, valid_bid):
    _, _, service, ops = _stack(tmp_path / "export.db", valid_bid)
    requirement = _create(service, valid_bid.bid_id, "Coverage")
    first = ops.handover_csv(valid_bid.bid_id)
    assert first == ops.handover_csv(valid_bid.bid_id)
    assert requirement.requirement_id in first
    assert "Engineering" in first
    assert "UNANSWERED" in first and "NOT_READY" in first


@pytest.mark.parametrize(
    ("status", "source", "received", "expected"),
    [
        ("CONFIRMED_COMPLIANT", "Manufacturer letter", "2026-08-31", True),
        ("CONFIRMED_COMPLIANT", "", "2026-08-31", False),
        ("CONFIRMED_COMPLIANT", "Manufacturer letter", "", False),
        ("CONFIRMED_COMPLIANT", "   ", "2026-08-31", False),
        ("CONFIRMED_COMPLIANT", "Manufacturer letter", "   ", False),
        ("NOT_APPLICABLE", None, None, True),
        ("AWAITING_MANUFACTURER", "Manufacturer letter", "2026-08-31", False),
        ("CONFIRMED_WITH_EXCEPTION", "Manufacturer letter", "2026-08-31", False),
        ("CLARIFICATION_REQUIRED", "Manufacturer letter", "2026-08-31", False),
        ("CANNOT_COMPLY", "Manufacturer letter", "2026-08-31", False),
        ("NOT_REVIEWED", None, None, False),
    ],
)
def test_manufacturer_confirmation_status_evidence_matrix(
    status: str, source: str | None, received: str | None, expected: bool
) -> None:
    assert manufacturer_confirmation_clear(status, source, received) is expected


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r", "\n"])
def test_csv_safe_cell_neutralizes_every_dangerous_prefix(prefix: str) -> None:
    value = prefix + "SUM(1,2)"
    assert csv_safe_cell(value) == "'" + value


def test_csv_safe_cell_preserves_normal_and_csv_writer_round_trips() -> None:
    values = ["ordinary", "comma,value", 'quote " value', "line one\nline two"]
    assert [csv_safe_cell(value) for value in values] == values
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerow(values)
    assert next(csv.reader(io.StringIO(output.getvalue()))) == values


def _covered_export(
    tmp_path: Path,
    valid_bid,
    *,
    response_source: str | None,
    response_date: date | None,
    add_empty_package: bool = False,
) -> str:
    db, _repository, requirement_service, ops = _stack(tmp_path / "covered.db", valid_bid)
    requirement = _create(requirement_service, valid_bid.bid_id, "=Customer formula")
    bids = BidRepository(db)
    vendor_repository = VendorDocumentRepository(db)
    vendor_service = VendorDocumentService(vendor_repository, bids)
    template = vendor_service.ensure_standard_template()
    package = vendor_service.create_package(
        SupplierPackageCreate(
            bid_id=valid_bid.bid_id,
            package_name="Package, with comma",
            package_code="=PKG-1",
            proposed_manufacturer="@Formula Manufacturer",
            internal_owner="Jason",
        ),
        "Jason",
    )
    vendor_requirement = vendor_service.create_requirement(
        CustomerRequirementCreate(
            package_id=package.package_id,
            customer_requirement_code="C-1",
            deliverable_title="Confirmation",
        ),
        "Jason",
    )
    package_csv = vendor_service.handover_csv(package.package_id)
    assert "'=PKG-1" in package_csv
    assert "'@Formula Manufacturer" in package_csv
    if response_source is None and response_date is None:
        with db._conn() as conn:
            conn.execute(
                "UPDATE vendor_bid_requirements SET verification_status='CONFIRMED_COMPLIANT' "
                "WHERE requirement_id=?",
                (vendor_requirement.requirement_id,),
            )
    else:
        vendor_service.update_verification(
            vendor_requirement.requirement_id,
            RequirementVerificationUpdate(
                expected_version=vendor_requirement.version,
                verification_status=VerificationStatus.CONFIRMED_COMPLIANT,
                proposed_manufacturer=package.proposed_manufacturer,
                response_source=response_source,
                response_received_date=response_date,
                commercial_impact="NONE",
                bid_disposition="NONE",
            ),
            "Jason",
        )
    assert template.template_id == package.template_id
    ops.link_manufacturer(valid_bid.bid_id, requirement.requirement_id, package.package_id, "Jason")
    Ops07WorkflowRepository(db).link_exact_evidence(
        valid_bid.bid_id,
        requirement.requirement_id,
        vendor_requirement.requirement_id,
        "Jason",
    )
    if add_empty_package:
        empty_package = vendor_service.create_package(
            SupplierPackageCreate(
                bid_id=valid_bid.bid_id,
                package_name="No response package",
                package_code="PKG-EMPTY",
                proposed_manufacturer="Silent manufacturer",
                internal_owner="Jason",
            ),
            "Jason",
        )
        ops.link_manufacturer(
            valid_bid.bid_id, requirement.requirement_id, empty_package.package_id, "Jason"
        )
    assert _manufacturer_coverage_clear(db, valid_bid.bid_id) is (
        response_source is not None and response_date is not None and not add_empty_package
    )
    return ops.handover_csv(valid_bid.bid_id)


def test_handover_uses_shared_evidence_predicate_and_neutralizes_csv(
    tmp_path: Path, valid_bid
) -> None:
    unevidenced = _covered_export(
        tmp_path / "without", valid_bid, response_source=None, response_date=None
    )
    row = next(csv.DictReader(io.StringIO(unevidenced)))
    assert row["manufacturer_verification"] == "CONFIRMED_COMPLIANT"
    assert row["readiness_state"] == "NOT_READY"
    assert "lacks response source and response date" in row["unresolved_action"]
    assert row["original_requirement"].startswith("'=")
    assert row["manufacturer_packages"].startswith("'")
    evidenced = _covered_export(
        tmp_path / "with",
        valid_bid,
        response_source="Manufacturer letter",
        response_date=date(2026, 8, 31),
    )
    evidenced_row = next(csv.DictReader(io.StringIO(evidenced)))
    assert evidenced_row["manufacturer_response_status"] == "Confirmed Compliant"
    assert evidenced_row["readiness_state"] == "NOT_READY"
    assert evidenced_row["internal_accountability_status"] == "Missing accountability"
    mixed = _covered_export(
        tmp_path / "mixed",
        valid_bid,
        response_source="Manufacturer letter",
        response_date=date(2026, 8, 31),
        add_empty_package=True,
    )
    mixed_row = next(csv.DictReader(io.StringIO(mixed)))
    assert mixed_row["readiness_state"] == "NOT_READY"
    assert "has no exact linked verification evidence" in mixed_row["unresolved_action"]
