"""Synthetic deterministic acceptance oracle for TASK-13."""

from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from core.bid_repository import BidRepository
from core.commercial import (
    Applicability,
    AssessmentVersion,
    BasisRole,
    CommercialCategory,
    CommercialItem,
    CommercialLink,
    CommercialRelation,
    CommercialReview,
    CommercialTargetType,
    CommercialTreatment,
    EvidenceBasis,
    ReviewDecision,
)
from core.commercial_repository import COMMERCIAL_MIGRATION_ID, CommercialRepository
from core.commercial_rules import calculate_commercial_gaps
from core.commercial_service import CommercialService
from core.database import Database
from core.enums import Actor, BidLevel, CustomerType
from core.schemas import Bid, Provenance


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contractiq-task13-") as root:
        now = datetime.now(UTC)
        db = Database(Path(root) / "commercial.db")
        bids = BidRepository(db)
        for suffix in ("0001", "0002"):
            bids.create_bid(
                Bid(
                    bid_id=f"B-2026-{suffix}",
                    customer="Synthetic",
                    customer_type=CustomerType.EPC,
                    project_name=f"Synthetic {suffix}",
                    sales_owner="sales",
                    bc_owner="bc",
                    release_date=date(2026, 1, 1),
                    customer_due_date=date(2026, 12, 1),
                    internal_due_date=date(2026, 11, 1),
                    estimated_value=Decimal("1"),
                    classification=BidLevel.LEVEL_1,
                    created_at=now,
                    updated_at=now,
                )
            )
        repo = CommercialRepository(db)
        service = CommercialService(repo)
        assert COMMERCIAL_MIGRATION_ID in {
            str(row[0])
            for row in db._conn().execute("SELECT migration_id FROM commercial_schema_migrations")
        }
        assert calculate_commercial_gaps([], as_of=date(2026, 1, 1), expected_bid_id="B-2026-0001")
        created = service.initialize_standard("B-2026-0001", "validator")
        assert len(created) == 14
        assert service.initialize_standard("B-2026-0001", "validator") == []
        provenance = Provenance(
            created_by=Actor.HUMAN,
            agent_name="validator",
            human_confirmed=True,
            confirmed_by="validator",
        )
        with db._conn() as conn:
            conn.execute(
                "CREATE TABLE requirements(requirement_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL)"
            )
            conn.execute("INSERT INTO requirements VALUES (?,?)", ("REQ-1", "B-2026-0001"))
        item = CommercialItem(
            bid_id="B-2026-0001",
            title="Synthetic freight basis",
            description="Exact basis",
            category=CommercialCategory.FREIGHT_LOGISTICS,
            basis_role=BasisRole.COMMERCIAL_FACTOR,
            owner="owner",
            provenance=provenance,
            created_at=now,
            updated_at=now,
            created_by="validator",
        )
        service.create(item, "validator")
        service.add_link(
            CommercialLink(
                bid_id=item.bid_id,
                commercial_item_id=item.commercial_item_id,
                target_type=CommercialTargetType.REQUIREMENT,
                target_id="REQ-1",
                relation=CommercialRelation.ADDRESSES_REQUIREMENT,
                created_at=now,
                created_by="validator",
            ),
            "validator",
        )
        service.activate(item.commercial_item_id, 1, "validator")
        assessment = AssessmentVersion(
            commercial_item_id=item.commercial_item_id,
            bid_id=item.bid_id,
            version_number=1,
            applicability=Applicability.APPLICABLE,
            treatment=CommercialTreatment.FIRM_PRICED,
            amount=Decimal("123.450000"),
            currency="CAD",
            evidence_basis=EvidenceBasis.BOUNDED_MANUAL_DECISION,
            rationale="Synthetic bounded basis",
            assessed_by="author",
            assessed_at=now,
            provenance=provenance,
            created_at=now,
        )
        service.add_assessment(assessment, "author")
        assert repo.assessments(item.commercial_item_id)[0]["amount_decimal"] == "123.450000"
        try:
            service.review(
                CommercialReview(
                    bid_id=item.bid_id,
                    commercial_item_id=item.commercial_item_id,
                    assessment_id=assessment.assessment_id,
                    decision=ReviewDecision.ACCEPTED,
                    reviewer="author",
                    reviewed_at=now,
                    provenance=provenance,
                ),
                "author",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("self-review accepted")
        service.review(
            CommercialReview(
                bid_id=item.bid_id,
                commercial_item_id=item.commercial_item_id,
                assessment_id=assessment.assessment_id,
                decision=ReviewDecision.ACCEPTED,
                reviewer="reviewer",
                reviewed_at=now,
                provenance=provenance,
            ),
            "reviewer",
        )
        gaps = service.gaps(item.bid_id, date(2026, 1, 1))
        assert all(
            g.code != "COMMERCIAL_ASSESSMENT_UNREVIEWED"
            or g.commercial_item_id != item.commercial_item_id
            for g in gaps
        )
        with db._conn() as conn:
            try:
                conn.execute(
                    "DELETE FROM commercial_assessments WHERE assessment_id=?",
                    (assessment.assessment_id,),
                )
            except Exception:
                pass
            else:
                raise AssertionError("assessment delete was permitted")
        print("TASK-13 validation: PASS")


if __name__ == "__main__":
    main()
