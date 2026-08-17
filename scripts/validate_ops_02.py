"""Deterministic OPS-02 work command-center validation."""

from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from core.bid_repository import BidRepository
from core.database import Database
from core.enums import BidLevel, CustomerType
from core.schemas import Bid
from core.work_item_repository import WorkItemRepository
from core.work_item_service import WorkItemService
from core.work_items import WorkRegisterFilter

AS_OF = date(2026, 8, 17)
NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


def _bid() -> Bid:
    return Bid(
        bid_id="B-2026-0001",
        customer="Synthetic customer",
        customer_type=CustomerType.EPC,
        project_name="Synthetic command center",
        sales_owner="Synthetic sales",
        bc_owner="Synthetic coordinator",
        release_date=date(2026, 8, 1),
        customer_due_date=date(2026, 8, 31),
        internal_due_date=date(2026, 8, 28),
        estimated_value=Decimal("1000"),
        classification=BidLevel.LEVEL_1,
        created_at=NOW,
        updated_at=NOW,
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contractiq-ops02-") as directory:
        db = Database(Path(directory) / "ops02.db")
        bids = BidRepository(db)
        repository = WorkItemRepository(db)
        ids = iter(UUID(int=value) for value in range(1, 30))
        service = WorkItemService(
            repository,
            bids,
            now_factory=lambda: NOW,
            id_factory=lambda: next(ids),
        )
        bid = _bid()
        bids.create_bid(bid)
        blocked = service.create_work_item(
            {
                "bid_id": bid.bid_id,
                "title": "Blocked",
                "status": "BLOCKED",
                "blocker_note": "Approval missing",
                "blocker_description": "Approval missing",
                "resolution_owner": "Commercial",
            },
            "validator",
        )
        overdue = service.create_work_item(
            {
                "title": "Standalone overdue",
                "category": "CUSTOMER_REQUEST",
                "responsibility_domain": "CUSTOMER_SOLUTION",
                "next_action_date": "2026-08-16",
                "contribution_candidate": True,
            },
            "validator",
        )
        service.create_work_item(
            {"title": "Due today", "due_date": AS_OF.isoformat()},
            "validator",
        )
        service.create_work_item(
            {"title": "Upcoming", "due_date": "2026-08-20"},
            "validator",
        )

        before_audit = len(bids.list_audit(None))
        current = service.get_work_register(WorkRegisterFilter(), as_of=AS_OF)
        standalone_attention = service.get_work_register(
            WorkRegisterFilter.model_validate(
                {
                    "category": "CUSTOMER_REQUEST",
                    "domain": "CUSTOMER_SOLUTION",
                    "context": "standalone",
                    "attention": "required",
                }
            ),
            as_of=AS_OF,
        )

        assert current[0].item == blocked
        assert current[1].item == overdue
        assert standalone_attention[0].context_label == "Standalone work"
        assert standalone_attention[0].item.contribution_candidate is True
        assert len({entry.item.work_item_id for entry in current}) == len(current)
        assert len(bids.list_audit(None)) == before_audit
        assert repository.get(overdue.work_item_id) == overdue
    print("OPS-02 validation: PASS")


if __name__ == "__main__":
    main()
