from datetime import UTC, date, datetime
from typing import cast
from unittest.mock import Mock
from uuid import UUID

import pytest
from pydantic import ValidationError

from core.bid_repository import BidRepository
from core.database import Database
from core.schemas import Bid
from core.work_item_repository import StaleWorkItemError, WorkItemRepository
from core.work_item_service import WorkItemService
from core.work_items import (
    WorkItemCreate,
    WorkItemKind,
    WorkItemPriority,
    WorkItemStatus,
)

NOW = datetime(2026, 8, 5, 14, 30, tzinfo=UTC)


def _service(
    db: Database,
    bid_repo: BidRepository,
    ids: list[int] | None = None,
) -> tuple[WorkItemService, WorkItemRepository]:
    values = iter(ids or list(range(1, 30)))
    repository = WorkItemRepository(db)
    return (
        WorkItemService(
            repository,
            bid_repo,
            now_factory=lambda: NOW,
            id_factory=lambda: UUID(int=next(values)),
        ),
        repository,
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"bid_id": "B-2026-0001", "title": "   "}, "title must be non-empty"),
        (
            {
                "bid_id": "B-2026-0001",
                "title": "Milestone",
                "kind": "MILESTONE",
            },
            "MILESTONE requires a due_date",
        ),
        (
            {
                "bid_id": "B-2026-0001",
                "title": "Waiting",
                "status": "WAITING",
                "waiting_on": "   ",
            },
            "WAITING requires waiting_on",
        ),
        (
            {
                "bid_id": "B-2026-0001",
                "title": "Blocked",
                "status": "BLOCKED",
                "blocker_note": "",
            },
            "BLOCKED requires blocker_note",
        ),
    ],
)
def test_input_invariants_are_rejected(payload: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        WorkItemCreate.model_validate(payload)


def test_invalid_enum_is_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkItemCreate.model_validate(
            {"bid_id": "B-2026-0001", "title": "Invalid", "status": "DONE"}
        )


def test_invalid_input_is_rejected_before_repository_access() -> None:
    work_repository = Mock()
    bid_repository = Mock()
    service = WorkItemService(
        cast(WorkItemRepository, work_repository),
        cast(BidRepository, bid_repository),
    )

    with pytest.raises(ValidationError, match="WAITING requires waiting_on"):
        service.create_work_item(
            {"bid_id": "B-2026-0001", "title": "Wait", "status": "WAITING"},
            "jason",
        )

    work_repository.create.assert_not_called()
    bid_repository.get_bid.assert_not_called()


def test_nonexistent_parent_is_rejected(
    tmp_db: Database,
    bid_repo: BidRepository,
) -> None:
    service, repository = _service(tmp_db, bid_repo)

    with pytest.raises(ValueError, match="Bid not found"):
        service.create_work_item(
            {"bid_id": "B-2026-9999", "title": "Orphan"},
            "jason",
        )

    assert repository.list() == []


def test_transition_fields_and_completion_timestamp_are_controlled_by_service(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    service, repository = _service(tmp_db, bid_repo)
    created = service.create_work_item(
        {
            "bid_id": valid_bid.bid_id,
            "title": "Coordinate submission",
            "priority": WorkItemPriority.HIGH,
        },
        "jason",
    )

    waiting = service.transition_work_item(
        created.work_item_id,
        {
            "expected_version": 1,
            "status": WorkItemStatus.WAITING,
            "waiting_on": " Customer legal ",
        },
        "jason",
    )
    assert waiting.waiting_on == "Customer legal"
    assert waiting.blocker_note is None

    blocked = service.transition_work_item(
        created.work_item_id,
        {
            "expected_version": 2,
            "status": WorkItemStatus.BLOCKED,
            "blocker_note": "Pricing approval missing",
        },
        "jason",
    )
    assert blocked.waiting_on is None
    assert blocked.blocker_note == "Pricing approval missing"

    completed = service.transition_work_item(
        created.work_item_id,
        {"expected_version": 3, "status": WorkItemStatus.COMPLETED},
        "jason",
    )
    assert completed.blocker_note is None
    assert completed.completed_at == NOW

    reopened = service.transition_work_item(
        created.work_item_id,
        {"expected_version": 4, "status": WorkItemStatus.OPEN},
        "jason",
    )
    assert reopened.completed_at is None
    assert reopened.status == WorkItemStatus.OPEN
    assert repository.get(created.work_item_id) == reopened


def test_edit_enforces_milestone_due_date_and_stale_version(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    service, _ = _service(tmp_db, bid_repo)
    created = service.create_work_item(
        {"bid_id": valid_bid.bid_id, "title": "Draft response"},
        "jason",
    )

    with pytest.raises(ValidationError, match="MILESTONE requires a due_date"):
        service.edit_work_item(
            created.work_item_id,
            {"expected_version": 1, "kind": WorkItemKind.MILESTONE},
            "jason",
        )

    edited = service.edit_work_item(
        created.work_item_id,
        {
            "expected_version": 1,
            "kind": WorkItemKind.MILESTONE,
            "due_date": date(2026, 8, 12),
            "title": " Final response ",
        },
        "jason",
    )
    assert edited.title == "Final response"
    assert edited.version == 2

    with pytest.raises(StaleWorkItemError):
        service.edit_work_item(
            created.work_item_id,
            {"expected_version": 1, "title": "Stale title"},
            "jason",
        )
