"""Deterministic OPS-05B validation using isolated SQLite storage."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from core.bid_repository import BidRepository
from core.database import Database
from core.schemas import Bid
from core.vendor_document_control import (
    BidDisposition,
    CommercialImpact,
    CustomerRequirementCreate,
    RequirementVerificationUpdate,
    SupplierPackageCreate,
    VerificationStatus,
)
from core.vendor_document_repository import (
    VENDOR_DOCUMENT_MIGRATION_ID,
    VendorDocumentRepository,
)
from core.vendor_document_service import VendorDocumentService


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contractiq-ops05b-validation-") as directory:
        db = Database(Path(directory) / "validation.db")
        bids = BidRepository(db)
        bid = Bid.model_validate(
            {
                "bid_id": "B-2026-0050",
                "customer": "Example EPCM",
                "customer_type": "epcm",
                "project_name": "Copper Expansion Bid",
                "sales_owner": "Sales",
                "bc_owner": "Jason",
                "release_date": "2026-08-20",
                "customer_due_date": "2026-10-01",
                "internal_due_date": "2026-09-25",
                "estimated_value": "5000000",
                "classification": "level_3",
                "created_at": "2026-08-20T09:00:00+00:00",
                "updated_at": "2026-08-20T09:00:00+00:00",
            }
        )
        bids.create_bid(bid)
        repository = VendorDocumentRepository(db)
        ids = iter(UUID(int=value) for value in range(1, 100))
        service = VendorDocumentService(
            repository,
            bids,
            now_factory=lambda: datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
            id_factory=lambda: next(ids),
        )
        service.ensure_standard_template()
        package = service.create_package(
            SupplierPackageCreate(
                bid_id=bid.bid_id,
                package_name="Medium-voltage switchgear",
                package_code="MV-SWG-01",
                customer_epcm=bid.customer,
                proposed_manufacturer="Example Switchgear",
                internal_owner="Package Engineer",
                source_vdrl_reference="VDRL-001",
            ),
            "Jason",
        )
        requirement = service.create_requirement(
            CustomerRequirementCreate(
                package_id=package.package_id,
                customer_requirement_code="A01",
                deliverable_title="General arrangement drawing",
                description="Customer-preserved requirement",
                requested_stages=["WITH_BID", "AFTER_AWARD"],
                original_contractual_timing="With bid and ARA +4 weeks",
                source_row_reference="Row 12",
            ),
            "Jason",
        )
        assert service.readiness(package.package_id).ready is False
        updated = service.update_verification(
            requirement.requirement_id,
            RequirementVerificationUpdate(
                expected_version=requirement.version,
                verification_status=VerificationStatus.CONFIRMED_WITH_EXCEPTION,
                proposed_manufacturer=package.proposed_manufacturer,
                response_source="Manufacturer email",
                internal_owner=package.internal_owner,
                response_received_date=datetime(2026, 8, 21, tzinfo=UTC).date(),
                committed_stages=["WITH_BID", "AFTER_AWARD"],
                committed_timing="With bid and 28 days after award",
                evidence_reference="EMAIL-001",
                proposed_exception="Certified issue follows award",
                commercial_impact=CommercialImpact.INCLUDED,
                bid_disposition=BidDisposition.QUALIFIED,
                disposition_approved=True,
                handover_note="Transfer commitment to execution owner",
            ),
            "Jason",
        )
        assert updated.description == "Customer-preserved requirement"
        assert service.readiness(package.package_id).ready is True
        handover = service.handover_csv(package.package_id)
        assert "Customer-preserved requirement" in handover
        assert "Manufacturer email" in handover
        assert "Certified issue follows award" in handover
        mismatch = "requirement_code,title,required,declared_required_total\n" + "\n".join(
            f"R{index},Requirement {index},yes,33" for index in range(1, 38)
        )
        preview = service.preview_import(package.package_id, mismatch)
        assert preview.valid is False and preview.actual_required_total == 37
        second = VendorDocumentRepository(db)
        assert isinstance(second, VendorDocumentRepository)
        with db._conn() as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            markers = conn.execute(
                "SELECT migration_id FROM vendor_document_schema_migrations"
            ).fetchall()
            assert [row[0] for row in markers] == [VENDOR_DOCUMENT_MIGRATION_ID]
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE name IN ('vendor_documents','vendor_document_submissions')"
                ).fetchone()[0]
                == 0
            )
    print("OPS-05B validation: PASS")


if __name__ == "__main__":
    main()
