"""Deterministic isolated acceptance checks for TASK-12."""

from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from core.bid_repository import BidRepository
from core.database import Database
from core.deliverable_repository import DELIVERABLE_MIGRATION_ID, DeliverableRepository
from core.deliverable_rules import calculate_deliverable_gaps, deliverable_metrics
from core.deliverable_service import DeliverableService
from core.deliverables import (
    Deliverable,
    DeliverableCriticality,
    DeliverableDirection,
    DeliverableLink,
    DeliverableRelation,
    DeliverableTargetType,
    DueBasis,
    LifecyclePhase,
)
from core.enums import Actor, BidLevel, CustomerType
from core.schemas import Bid, Provenance


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contractiq-task12-") as directory:
        db = Database(Path(directory) / "validation.db")
        bids = BidRepository(db)
        now = datetime.now(UTC)
        bid = Bid(
            bid_id="B-2026-0001",
            customer="Synthetic Customer",
            customer_type=CustomerType.END_USER,
            project_name="Synthetic Vendor Controls",
            sales_owner="Sales",
            bc_owner="BC",
            release_date=date(2026, 1, 1),
            customer_due_date=date(2026, 12, 1),
            internal_due_date=date(2026, 11, 1),
            estimated_value=Decimal("1000"),
            classification=BidLevel.LEVEL_1,
            created_at=now,
            updated_at=now,
        )
        bids.create_bid(bid)
        repository = DeliverableRepository(db)
        service = DeliverableService(repository)
        provenance = Provenance(
            created_by=Actor.HUMAN,
            agent_name="validator",
            human_confirmed=True,
            confirmed_by="validator",
        )
        item = Deliverable(
            bid_id=bid.bid_id,
            title="Synthetic schedule",
            description="Provide schedule",
            category="SCHEDULE",
            criticality=DeliverableCriticality.MANDATORY,
            lifecycle_phase=LifecyclePhase.WITH_BID,
            direction=DeliverableDirection.INTERNAL,
            due_basis=DueBasis.FIXED_DATE,
            fixed_due_date=date(2026, 12, 20),
            owner="owner",
            recipient="customer",
            provenance=provenance,
            created_at=now,
            updated_at=now,
            created_by="validator",
        )
        service.create(item, "validator")
        with db._conn() as conn:
            conn.execute(
                "CREATE TABLE requirements (requirement_id TEXT PRIMARY KEY, bid_id TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO requirements VALUES (?,?)", ("synthetic-requirement", bid.bid_id)
            )
        service.add_link(
            DeliverableLink(
                bid_id=bid.bid_id,
                deliverable_id=item.deliverable_id,
                target_type=DeliverableTargetType.REQUIREMENT,
                target_id="synthetic-requirement",
                relation=DeliverableRelation.CREATED_BY_REQUIREMENT,
                created_at=now,
                created_by="validator",
            ),
            "validator",
        )
        service.activate(item.deliverable_id, 1, "validator")
        assert repository.list(bid.bid_id)[0]["workflow_state"] == "ACTIVE"
        gaps = calculate_deliverable_gaps(
            repository.list(bid.bid_id),
            as_of=date(2026, 12, 15),
            links={item.deliverable_id: repository.links(item.deliverable_id)},
        )
        assert "DELIVERABLE_DUE_SOON" in {gap.code for gap in gaps}
        assert deliverable_metrics(gaps, repository.list(bid.bid_id))["total_deliverables"] == 1
        with db._conn() as conn:
            assert conn.execute(
                "SELECT migration_id FROM deliverable_schema_migrations WHERE migration_id=?",
                (DELIVERABLE_MIGRATION_ID,),
            ).fetchone()
        print("TASK-12 validation: PASS")


if __name__ == "__main__":
    main()
