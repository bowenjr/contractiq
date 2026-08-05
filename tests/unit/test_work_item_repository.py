import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest

from core.bid_repository import BidRepository
from core.database import Database
from core.enums import ApprovalType, Gate, GateStatus
from core.schemas import Approval, AuditEntry, Bid, GateRecord, Provenance
from core.work_item_repository import (
    WORK_ITEM_MIGRATION_ID,
    WorkItemRepository,
)
from core.work_item_service import WorkItemService
from core.work_items import WorkItem, WorkItemKind, WorkItemPriority, WorkItemStatus

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)


def _item(bid: Bid, *, item_id: int = 1, title: str = "Prepare response") -> WorkItem:
    return WorkItem(
        work_item_id=f"WI-{UUID(int=item_id)}",
        bid_id=bid.bid_id,
        kind=WorkItemKind.TASK,
        title=title,
        status=WorkItemStatus.OPEN,
        priority=WorkItemPriority.NORMAL,
        due_date=date(2026, 8, 5),
        created_at=NOW,
        updated_at=NOW,
        version=1,
        provenance=Provenance.from_human("jason"),
    )


def _audit(bid: Bid, item: WorkItem, *, entry_id: str) -> AuditEntry:
    return AuditEntry(
        entry_id=entry_id,
        bid_id=bid.bid_id,
        actor="jason",
        action="work_item_created",
        detail=f'{{"work_item_id":"{item.work_item_id}"}}',
        timestamp=NOW,
    )


def test_migration_succeeds_on_new_database(tmp_path: Path) -> None:
    db = Database(tmp_path / "new.db")
    BidRepository(db)

    WorkItemRepository(db)

    with db._conn() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(work_items)")}
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(work_items)")}
        foreign_keys = conn.execute("PRAGMA foreign_key_list(work_items)").fetchall()
    assert WORK_ITEM_MIGRATION_ID == "task_07_work_items_v1"
    assert {"work_item_id", "bid_id", "version", "provenance_json"} <= columns
    assert {"idx_work_items_bid_status_due", "idx_work_items_status_due"} <= indexes
    assert any(row["table"] == "bids" and row["from"] == "bid_id" for row in foreign_keys)


def test_migration_from_task06_schema_preserves_representative_data(
    tmp_path: Path,
    valid_bid: Bid,
    valid_provenance: Provenance,
) -> None:
    db = Database(tmp_path / "task06.db")
    bid_repo = BidRepository(db)
    bid_repo.create_bid(valid_bid)
    db.create_document({"id": "DOC-T06", "filename": "baseline.pdf"})
    bid_repo.attach_document_to_bid("DOC-T06", valid_bid.bid_id)
    bid_repo.create_approval(
        Approval(
            approval_id="APP-T06",
            bid_id=valid_bid.bid_id,
            approval_type=ApprovalType.MARGIN,
            provenance=valid_provenance,
        )
    )
    bid_repo.upsert_gate_record(
        GateRecord(bid_id=valid_bid.bid_id, gate=Gate.G4, status=GateStatus.IN_REVIEW)
    )
    bid_repo.append_audit(
        AuditEntry(
            entry_id="AUD-T06",
            bid_id=valid_bid.bid_id,
            actor="system",
            action="baseline",
            detail="TASK-06 representative data",
            timestamp=NOW,
        )
    )

    first = WorkItemRepository(db)
    second = WorkItemRepository(db)

    assert first.list() == second.list() == []
    assert bid_repo.get_bid(valid_bid.bid_id) == valid_bid
    assert bid_repo.list_documents_for_bid(valid_bid.bid_id)[0]["id"] == "DOC-T06"
    assert bid_repo.list_approvals(valid_bid.bid_id)[0].approval_id == "APP-T06"
    assert bid_repo.get_gate_record(valid_bid.bid_id, Gate.G4) is not None
    assert bid_repo.list_audit(valid_bid.bid_id)[0].entry_id == "AUD-T06"


def test_create_edit_transition_persist_and_append_audit(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    ids = iter(range(1, 20))
    repository = WorkItemRepository(tmp_db)
    service = WorkItemService(
        repository,
        bid_repo,
        now_factory=lambda: NOW,
        id_factory=lambda: UUID(int=next(ids)),
    )

    created = service.create_work_item(
        {"bid_id": valid_bid.bid_id, "title": "Initial", "due_date": "2026-08-06"},
        "jason",
    )
    edited = service.edit_work_item(
        created.work_item_id,
        {"expected_version": 1, "title": "Edited", "priority": "HIGH"},
        "jason",
    )
    completed = service.transition_work_item(
        created.work_item_id,
        {"expected_version": 2, "status": "COMPLETED"},
        "jason",
    )

    assert repository.get(created.work_item_id) == completed
    assert edited.priority == WorkItemPriority.HIGH
    assert completed.completed_at == NOW
    entries = bid_repo.list_audit(valid_bid.bid_id)
    assert [entry.action for entry in entries] == [
        "work_item_created",
        "work_item_updated",
        "work_item_status_transitioned",
    ]
    assert all(created.work_item_id in entry.detail for entry in entries)
    assert '"before": null' in entries[0].detail
    assert '"before": {' in entries[1].detail


def test_audit_failure_rolls_back_work_item_write(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    repository = WorkItemRepository(tmp_db)
    item = _item(valid_bid)
    duplicate_id = "AUD-DUPLICATE"
    bid_repo.append_audit(_audit(valid_bid, item, entry_id=duplicate_id))

    with pytest.raises(sqlite3.IntegrityError):
        repository.create(item, _audit(valid_bid, item, entry_id=duplicate_id))

    assert repository.get(item.work_item_id) is None
    assert [entry.entry_id for entry in bid_repo.list_audit(valid_bid.bid_id)] == [duplicate_id]


def test_work_item_failure_does_not_leave_orphan_audit(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    repository = WorkItemRepository(tmp_db)
    item = _item(valid_bid)
    repository.create(item, _audit(valid_bid, item, entry_id="AUD-FIRST"))

    with pytest.raises(sqlite3.IntegrityError):
        repository.create(item, _audit(valid_bid, item, entry_id="AUD-ORPHAN"))

    assert [entry.entry_id for entry in bid_repo.list_audit(valid_bid.bid_id)] == ["AUD-FIRST"]


def test_supported_repository_has_no_hard_delete_operation(tmp_db: Database) -> None:
    repository = WorkItemRepository(tmp_db)

    assert not hasattr(repository, "delete")
    assert not hasattr(repository, "hard_delete")
