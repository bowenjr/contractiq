"""Synthetic deterministic TASK-14 acceptance oracle."""

from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from core.bid_repository import BidRepository
from core.contract_risk import (
    Consequence,
    ContractIssue,
    ExposureBasis,
    Likelihood,
    ProposedDisposition,
    ReviewDecision,
    RiskAssessment,
    RiskCategory,
    RiskRating,
    RiskReview,
    RiskSource,
    SourceType,
    risk_rating,
)
from core.contract_risk_repository import RISK_MIGRATION_ID, ContractRiskRepository
from core.contract_risk_rules import calculate_risk_gaps
from core.contract_risk_service import ContractRiskService
from core.database import Database
from core.enums import Actor, BidLevel, CustomerType
from core.schemas import Bid, Provenance


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contractiq-task14-") as root:
        now = datetime.now(UTC)
        db = Database(Path(root) / "risk.db")
        bids = BidRepository(db)
        for suffix in ("0001", "0002"):
            bids.create_bid(
                Bid(
                    bid_id=f"B-2026-{suffix}",
                    customer="Synthetic",
                    customer_type=CustomerType.EPC,
                    project_name="Risk",
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
        repo = ContractRiskRepository(db)
        service = ContractRiskService(repo)
        assert RISK_MIGRATION_ID in {
            r[0]
            for r in db._conn().execute("SELECT migration_id FROM contract_risk_schema_migrations")
        }
        provenance = Provenance(
            created_by=Actor.HUMAN,
            agent_name="validator",
            human_confirmed=True,
            confirmed_by="validator",
        )
        with db._conn() as c:
            c.execute(
                "CREATE TABLE requirements(requirement_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL)"
            )
            c.execute("INSERT INTO requirements VALUES (?,?)", ("REQ-1", "B-2026-0001"))
        issue = ContractIssue(
            bid_id="B-2026-0001",
            issue_code="REQ-DEV-1",
            title="Synthetic deviation",
            summary="Synthetic contract deviation",
            owner="owner",
            materiality="MATERIAL",
            provenance=provenance,
            created_at=now,
            updated_at=now,
            created_by="validator",
        )
        service.create(issue, "validator")
        service.add_source(
            RiskSource(
                bid_id=issue.bid_id,
                issue_id=issue.issue_id,
                source_type=SourceType.BOUNDED_MANUAL_SOURCE,
                source_title="Synthetic clause note",
                issuer_role="customer",
                source_date=date(2026, 1, 1),
                locator="Clause 1",
                rationale="Managed source unavailable",
                created_at=now,
                created_by="validator",
            ),
            "validator",
        )
        assessment = RiskAssessment(
            issue_id=issue.issue_id,
            bid_id=issue.bid_id,
            version_number=1,
            category=RiskCategory.LIMITATION_OF_LIABILITY,
            customer_position="Customer position",
            company_position="Proposed company position",
            target_position="Target",
            business_impact="Synthetic impact",
            affected_functions=("COMMERCIAL",),
            disposition=ProposedDisposition.PROPOSE_DEVIATION,
            likelihood=Likelihood.LIKELY,
            consequence=Consequence.MAJOR,
            exposure_basis=ExposureBasis.MONETARY_RANGE,
            minimum=Decimal("1.000"),
            most_likely=Decimal("2.000"),
            maximum=Decimal("3.000"),
            currency="CAD",
            rationale="Synthetic",
            assessed_by="author",
            assessed_at=now,
            provenance=provenance,
            created_at=now,
        )
        assert risk_rating(assessment.likelihood, assessment.consequence) == (12, RiskRating.HIGH)
        service.assessment(assessment, "author")
        service.activate(issue.issue_id, 1, "validator")
        try:
            service.review(
                RiskReview(
                    bid_id=issue.bid_id,
                    issue_id=issue.issue_id,
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
            raise AssertionError("self review accepted")
        service.review(
            RiskReview(
                bid_id=issue.bid_id,
                issue_id=issue.issue_id,
                assessment_id=assessment.assessment_id,
                decision=ReviewDecision.ACCEPTED,
                reviewer="reviewer",
                reviewed_at=now,
                provenance=provenance,
            ),
            "reviewer",
        )
        service.gaps(issue.bid_id, date(2026, 1, 1))
        assert (
            any(
                g.code == "CONTRACT_RISK_PAST_DUE"
                for g in calculate_risk_gaps(
                    [{**repo.list(issue.bid_id)[0]}],
                    as_of=date(2026, 1, 1),
                    assessments={issue.issue_id: repo.assessments(issue.issue_id)},
                    sources={issue.issue_id: repo.sources(issue.issue_id)},
                    reviews={issue.issue_id: repo.reviews(issue.issue_id)},
                )
            )
            is False
        )
        with db._conn() as c:
            try:
                c.execute(
                    "DELETE FROM contract_risk_assessments WHERE assessment_id=?",
                    (assessment.assessment_id,),
                )
            except Exception:
                pass
            else:
                raise AssertionError("assessment delete permitted")
        print("TASK-14 validation: PASS")


if __name__ == "__main__":
    main()
