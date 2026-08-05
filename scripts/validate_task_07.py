"""Deterministic acceptance validation for TASK-07."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import Mock
from uuid import UUID

from pydantic import ValidationError

from core.bid_repository import BidRepository
from core.database import Database
from core.enums import BidLevel, CustomerType
from core.schemas import Bid
from core.work_item_repository import WORK_ITEM_MIGRATION_ID, WorkItemRepository
from core.work_item_service import MyDayService, WorkItemService
from core.work_items import WorkItemStatus

AS_OF = date(2026, 8, 5)
NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)


def _bid() -> Bid:
    return Bid(
        bid_id="B-2026-0700",
        customer="Validation EPC",
        customer_type=CustomerType.EPC,
        project_name="TASK-07 Validation Bid",
        sales_owner="Sales Owner",
        bc_owner="Bid Coordinator",
        release_date=date(2026, 7, 1),
        customer_due_date=date(2026, 8, 31),
        internal_due_date=date(2026, 8, 28),
        estimated_value=Decimal("750000"),
        classification=BidLevel.LEVEL_2,
        created_at=NOW,
        updated_at=NOW,
    )


def _prove_pre_database_validation() -> None:
    work_repository = Mock(spec=WorkItemRepository)
    bid_repository = Mock(spec=BidRepository)
    service = WorkItemService(
        cast(WorkItemRepository, work_repository),
        cast(BidRepository, bid_repository),
    )
    try:
        service.create_work_item(
            {
                "bid_id": "B-2026-0700",
                "title": "Invalid waiting item",
                "status": "WAITING",
            },
            "validation",
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("Invalid WAITING request was accepted")
    work_repository.create.assert_not_called()
    bid_repository.get_bid.assert_not_called()


def main() -> None:
    with TemporaryDirectory(prefix="contractiq-task07-") as temp_dir:
        db_path = Path(temp_dir) / "validation.db"
        db = Database(db_path)
        bid_repository = BidRepository(db)
        work_repository = WorkItemRepository(db)
        bid = _bid()
        bid_repository.create_bid(bid)
        ids = iter(UUID(int=value) for value in range(1, 40))
        work_service = WorkItemService(
            work_repository,
            bid_repository,
            now_factory=lambda: NOW,
            id_factory=lambda: next(ids),
        )

        overdue = work_service.create_work_item(
            {
                "bid_id": bid.bid_id,
                "title": "Submit overdue clarification",
                "priority": "HIGH",
                "due_date": "2026-08-04",
            },
            "validation",
        )
        work_service.create_work_item(
            {
                "bid_id": bid.bid_id,
                "kind": "MILESTONE",
                "title": "Pricing freeze",
                "due_date": "2026-08-05",
            },
            "validation",
        )
        work_service.create_work_item(
            {
                "bid_id": bid.bid_id,
                "title": "Prepare executive brief",
                "due_date": "2026-08-06",
            },
            "validation",
        )
        work_service.create_work_item(
            {
                "bid_id": bid.bid_id,
                "title": "Receive customer answers",
                "status": "WAITING",
                "waiting_on": "Customer procurement",
                "due_date": "2026-08-04",
            },
            "validation",
        )
        work_service.create_work_item(
            {
                "bid_id": bid.bid_id,
                "title": "Resolve supplier pricing",
                "status": "BLOCKED",
                "blocker_note": "Supplier quote has not arrived",
                "due_date": "2026-08-03",
                "priority": "CRITICAL",
            },
            "validation",
        )
        work_service.create_work_item(
            {
                "bid_id": bid.bid_id,
                "title": "Completed setup",
                "status": "COMPLETED",
            },
            "validation",
        )

        projection = MyDayService(
            work_repository,
            bid_repository,
            db,
        ).get_my_day(as_of=AS_OF)
        assert [entry.item.title for entry in projection.blocked] == ["Resolve supplier pricing"]
        assert [entry.item.title for entry in projection.waiting] == ["Receive customer answers"]
        assert [entry.item.title for entry in projection.overdue] == [
            "Submit overdue clarification"
        ]
        assert [entry.item.title for entry in projection.due_today] == ["Pricing freeze"]
        assert [entry.item.title for entry in projection.upcoming] == ["Prepare executive brief"]
        assert projection.blocked[0].is_overdue is True
        assert projection.waiting[0].is_overdue is True
        assert projection.counts.overdue == 3
        assert projection.counts.due_today == 1
        assert projection.counts.waiting == 1
        assert projection.counts.blocked == 1
        assert projection.counts.readiness_holds == 1
        assert projection.readiness_holds[0].bid_id == bid.bid_id

        completed = work_service.transition_work_item(
            overdue.work_item_id,
            {"expected_version": overdue.version, "status": WorkItemStatus.COMPLETED},
            "validation",
        )
        assert completed.completed_at == NOW
        assert work_repository.get(overdue.work_item_id) == completed
        audit_entries = bid_repository.list_audit(bid.bid_id)
        assert len(audit_entries) == 7
        assert audit_entries[-1].action == "work_item_status_transitioned"
        assert overdue.work_item_id in audit_entries[-1].detail

        refreshed = MyDayService(
            work_repository,
            bid_repository,
            db,
        ).get_my_day(as_of=AS_OF)
        assert all(entry.item.work_item_id != overdue.work_item_id for entry in refreshed.overdue)
        _prove_pre_database_validation()

        print(
            "TASK-07 validation passed: "
            f"migration={WORK_ITEM_MIGRATION_ID}; "
            "fixed_date=2026-08-05; buckets=5 active; "
            "overdue_flags=3; readiness_holds=1; "
            "atomic_completion_audit=committed; pre_database_validation=passed"
        )


if __name__ == "__main__":
    main()
