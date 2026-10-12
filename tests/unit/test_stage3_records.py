"""Unit coverage for Stage 3 execution overlays, notes, schema, and audit."""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest

from core.bid_execution import ExecutionSubjectKind, PrepStatus
from core.bid_execution_repository import BidExecutionRepository
from core.bid_execution_service import BidExecutionService
from core.bid_execution_subjects import ExecutionSubjectResolver
from core.bid_repository import BidRepository
from core.database import Database
from core.deliverable_repository import DeliverableRepository
from core.deliverables import (
    Deliverable,
    DeliverableCriticality,
    DeliverableDirection,
    DeliverableState,
    LifecyclePhase,
)
from core.document_repository import DocumentRepository
from core.schemas import Bid, Provenance
from core.vendor_document_repository import VendorDocumentRepository
from core.work_item_repository import WorkItemRepository
from core.work_item_service import WorkItemService

NOW = datetime(2026, 7, 10, 14, 30, tzinfo=UTC)


def _service(
    db: Database,
    bid_repository: BidRepository,
) -> tuple[BidExecutionService, BidExecutionRepository]:
    numbers: Iterator[int] = iter(range(1, 100))
    repository = BidExecutionRepository(db)
    documents = DocumentRepository(db)
    work_items = WorkItemRepository(db)
    deliverables = DeliverableRepository(db)
    vendor_documents = VendorDocumentRepository(db)
    return (
        BidExecutionService(
            repository,
            bid_repository,
            documents,
            ExecutionSubjectResolver(work_items, deliverables, vendor_documents),
            now_factory=lambda: NOW,
            id_factory=lambda: UUID(int=next(numbers)),
        ),
        repository,
    )


def _returnable(db: Database, bid: Bid, deliverable_id: str, title: str = "Bid form") -> None:
    DeliverableRepository(db).create(
        Deliverable(
            deliverable_id=deliverable_id,
            bid_id=bid.bid_id,
            title=title,
            description="Required Bid returnable",
            category="Submission",
            criticality=DeliverableCriticality.MANDATORY,
            lifecycle_phase=LifecyclePhase.WITH_BID,
            direction=DeliverableDirection.INTERNAL,
            workflow_state=DeliverableState.ACTIVE,
            provenance=Provenance.from_human("jason"),
            created_at=NOW,
            updated_at=NOW,
            created_by="jason",
        ),
        "jason",
    )


def _work_item(db: Database, bids: BidRepository, bid: Bid, title: str = "Review package") -> str:
    service = WorkItemService(
        WorkItemRepository(db),
        bids,
        now_factory=lambda: NOW,
        id_factory=lambda: UUID(int=900),
    )
    return service.create_work_item({"bid_id": bid.bid_id, "title": title}, "jason").work_item_id


def _audit_count(db: Database, action: str = "bid_execution_record_saved") -> int:
    with db._conn() as conn:
        return int(
            conn.execute("SELECT COUNT(*) FROM audit_log WHERE action=?", (action,)).fetchone()[0]
        )


def test_schema_migration_is_idempotent_on_an_existing_database(
    tmp_path: Path, valid_bid: Bid
) -> None:
    db = Database(tmp_path / "existing.db")
    bids = BidRepository(db)
    bids.create_bid(valid_bid)
    with db._conn() as conn:
        conn.execute("CREATE TABLE existing_stage2_data (value TEXT NOT NULL)")
        conn.execute("INSERT INTO existing_stage2_data VALUES ('preserved')")

    BidExecutionRepository(db)
    BidExecutionRepository(db)

    with db._conn() as conn:
        tables = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        triggers = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        indexes = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        preserved = conn.execute("SELECT value FROM existing_stage2_data").fetchone()[0]
    assert {
        "bid_execution_records",
        "bid_execution_notes",
        "bid_execution_dependencies",
    } <= tables
    assert {
        "bid_execution_record_no_delete",
        "bid_execution_note_no_update",
        "bid_execution_note_no_delete",
        "bid_execution_dependency_no_delete",
        "bid_execution_dependency_remove_once",
    } <= triggers
    assert "uq_bid_execution_dependency_active" in indexes
    assert preserved == "preserved"


def test_optimistic_version_conflict_requires_reload(
    tmp_db: Database, bid_repo: BidRepository, valid_bid: Bid
) -> None:
    bid_repo.create_bid(valid_bid)
    _returnable(tmp_db, valid_bid, "DEL-1")
    service, _ = _service(tmp_db, bid_repo)
    created = service.set_record(
        valid_bid.bid_id,
        ExecutionSubjectKind.RETURNABLE,
        "DEL-1",
        {"expected_version": 0, "responsible": "Bid Manager"},
        "jason",
    )
    updated = service.set_record(
        valid_bid.bid_id,
        ExecutionSubjectKind.RETURNABLE,
        "DEL-1",
        {"expected_version": created.version, "responsible": "Proposal Lead"},
        "jason",
    )

    with pytest.raises(ValueError, match="reload the item"):
        service.set_record(
            valid_bid.bid_id,
            ExecutionSubjectKind.RETURNABLE,
            "DEL-1",
            {"expected_version": created.version, "responsible": "Stale edit"},
            "jason",
        )

    assert updated.version == 2
    assert service.get_record(valid_bid.bid_id, "RETURNABLE", "DEL-1") == updated


def test_audit_failure_rolls_back_the_record_mutation(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bid_repo.create_bid(valid_bid)
    _returnable(tmp_db, valid_bid, "DEL-ROLLBACK")
    service, repository = _service(tmp_db, bid_repo)

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(repository, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        service.set_record(
            valid_bid.bid_id,
            ExecutionSubjectKind.RETURNABLE,
            "DEL-ROLLBACK",
            {"expected_version": 0, "responsible": "Bid Manager"},
            "jason",
        )

    assert repository.get_record(ExecutionSubjectKind.RETURNABLE, "DEL-ROLLBACK") is None
    assert _audit_count(tmp_db) == 0


def test_notes_are_append_only_and_each_append_is_audited(
    tmp_db: Database, bid_repo: BidRepository, valid_bid: Bid
) -> None:
    bid_repo.create_bid(valid_bid)
    _returnable(tmp_db, valid_bid, "DEL-NOTES")
    service, _ = _service(tmp_db, bid_repo)
    first = service.add_note(valid_bid.bid_id, "RETURNABLE", "DEL-NOTES", "First update", "jason")
    second = service.add_note(valid_bid.bid_id, "RETURNABLE", "DEL-NOTES", "Correction", "jason")

    assert [
        note.body for note in service.list_notes(valid_bid.bid_id, "RETURNABLE", "DEL-NOTES")
    ] == [
        "First update",
        "Correction",
    ]
    assert _audit_count(tmp_db, "bid_execution_note_added") == 2
    with tmp_db._conn() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE bid_execution_notes SET body='rewritten' WHERE note_id=?",
                (first.note_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute("DELETE FROM bid_execution_notes WHERE note_id=?", (second.note_id,))


def test_hard_delete_is_blocked_for_records_and_dependencies(
    tmp_db: Database, bid_repo: BidRepository, valid_bid: Bid
) -> None:
    bid_repo.create_bid(valid_bid)
    _returnable(tmp_db, valid_bid, "DEL-DELETE")
    work_item_id = _work_item(tmp_db, bid_repo, valid_bid)
    service, _ = _service(tmp_db, bid_repo)
    record = service.set_record(
        valid_bid.bid_id,
        "RETURNABLE",
        "DEL-DELETE",
        {"expected_version": 0, "responsible": "Bid Manager"},
        "jason",
    )
    with tmp_db._conn() as conn:
        conn.execute(
            "INSERT INTO bid_execution_dependencies VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "DEP-1",
                valid_bid.bid_id,
                "RETURNABLE",
                "DEL-DELETE",
                "WORK_ITEM",
                work_item_id,
                None,
                NOW.isoformat(),
                "jason",
                None,
                None,
            ),
        )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute(
                "DELETE FROM bid_execution_records WHERE execution_id=?",
                (record.execution_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute("DELETE FROM bid_execution_dependencies WHERE dependency_id='DEP-1'")


def test_unchanged_resave_is_a_no_op(
    tmp_db: Database, bid_repo: BidRepository, valid_bid: Bid
) -> None:
    bid_repo.create_bid(valid_bid)
    _returnable(tmp_db, valid_bid, "DEL-NOOP")
    service, _ = _service(tmp_db, bid_repo)
    data = {
        "expected_version": 0,
        "responsible": "Bid Manager",
        "contributors": ["Engineering", "Commercial"],
        "internal_due_date": date(2026, 7, 20),
        "prep_status": PrepStatus.IN_PROGRESS,
        "priority": "HIGH",
    }
    created = service.set_record(valid_bid.bid_id, "RETURNABLE", "DEL-NOOP", data, "jason")
    before_audits = _audit_count(tmp_db)
    data["expected_version"] = created.version

    resaved = service.set_record(valid_bid.bid_id, "RETURNABLE", "DEL-NOOP", data, "jason")

    assert resaved == created
    assert resaved.version == 1
    assert _audit_count(tmp_db) == before_audits


def test_due_date_after_customer_deadline_is_refused_with_fix(
    tmp_db: Database, bid_repo: BidRepository, valid_bid: Bid
) -> None:
    bid_repo.create_bid(valid_bid)
    _returnable(tmp_db, valid_bid, "DEL-LATE")
    service, repository = _service(tmp_db, bid_repo)

    with pytest.raises(ValueError, match="choose a date on or before the customer deadline"):
        service.set_record(
            valid_bid.bid_id,
            "RETURNABLE",
            "DEL-LATE",
            {"expected_version": 0, "internal_due_date": date(2026, 8, 1)},
            "jason",
        )

    assert repository.get_record(ExecutionSubjectKind.RETURNABLE, "DEL-LATE") is None


def test_returnable_status_requirements_and_evidence_existence(
    tmp_db: Database, bid_repo: BidRepository, valid_bid: Bid
) -> None:
    bid_repo.create_bid(valid_bid)
    _returnable(tmp_db, valid_bid, "DEL-STATUS")
    service, _ = _service(tmp_db, bid_repo)
    base = (valid_bid.bid_id, ExecutionSubjectKind.RETURNABLE, "DEL-STATUS")

    with pytest.raises(ValueError, match="Complete without evidence"):
        service.set_record(*base, {"expected_version": 0, "prep_status": "COMPLETE"}, "jason")
    with pytest.raises(ValueError, match="Blocked without a reason"):
        service.set_record(*base, {"expected_version": 0, "prep_status": "BLOCKED"}, "jason")
    with pytest.raises(ValueError, match="Not required without a note"):
        service.set_record(*base, {"expected_version": 0, "prep_status": "NOT_REQUIRED"}, "jason")
    with pytest.raises(ValueError, match="does not exist"):
        service.set_record(
            *base,
            {
                "expected_version": 0,
                "prep_status": "COMPLETE",
                "evidence_document_version_id": "DV-MISSING",
            },
            "jason",
        )

    complete = service.set_record(
        *base,
        {
            "expected_version": 0,
            "prep_status": "COMPLETE",
            "completion_evidence": "Included in final response volume 2",
        },
        "jason",
    )
    assert complete.prep_status is PrepStatus.COMPLETE


def test_overlay_does_not_infer_owner_date_or_status_from_register(
    tmp_db: Database, bid_repo: BidRepository, valid_bid: Bid
) -> None:
    bid_repo.create_bid(valid_bid)
    deliverables = DeliverableRepository(tmp_db)
    deliverables.create(
        Deliverable(
            deliverable_id="DEL-NO-INFERENCE",
            bid_id=valid_bid.bid_id,
            title="Bid form",
            description="Signed bid form",
            category="Submission",
            criticality=DeliverableCriticality.MANDATORY,
            lifecycle_phase=LifecyclePhase.WITH_BID,
            direction=DeliverableDirection.INTERNAL,
            workflow_state=DeliverableState.ACTIVE,
            owner="Register Owner",
            provenance=Provenance.from_human("jason"),
            created_at=NOW,
            updated_at=NOW,
            created_by="jason",
        ),
        "jason",
    )
    service, _ = _service(tmp_db, bid_repo)

    record = service.set_record(
        valid_bid.bid_id,
        "RETURNABLE",
        "DEL-NO-INFERENCE",
        {"expected_version": 0, "contributors": ["Engineering"]},
        "jason",
    )

    assert record.responsible is None
    assert record.internal_due_date is None
    assert record.prep_status is None
    assert record.priority is None
    assert record.contributors == ["Engineering"]
    assert ExecutionSubjectKind.RETURNABLE.label == "Returnable"
    assert PrepStatus.NOT_STARTED.label == "Not started"


def test_missing_subject_refuses_overlay_and_note_access(
    tmp_db: Database, bid_repo: BidRepository, valid_bid: Bid
) -> None:
    bid_repo.create_bid(valid_bid)
    service, repository = _service(tmp_db, bid_repo)

    with pytest.raises(ValueError, match="was not found; choose an existing returnable"):
        service.set_record(
            valid_bid.bid_id,
            "RETURNABLE",
            "DEL-MISSING",
            {"expected_version": 0, "responsible": "Bid Manager"},
            "jason",
        )
    with pytest.raises(ValueError, match="was not found; choose an existing returnable"):
        service.add_note(valid_bid.bid_id, "RETURNABLE", "DEL-MISSING", "Update", "jason")
    with pytest.raises(ValueError, match="was not found; choose an existing returnable"):
        service.list_notes(valid_bid.bid_id, "RETURNABLE", "DEL-MISSING")

    assert repository.get_record(ExecutionSubjectKind.RETURNABLE, "DEL-MISSING") is None
    assert _audit_count(tmp_db) == 0


def test_cross_bid_subject_refuses_overlay_and_note_access(
    tmp_db: Database, bid_repo: BidRepository, valid_bid: Bid
) -> None:
    other_bid = valid_bid.model_copy(
        update={"bid_id": "B-2026-0002", "project_name": "Other Project"}
    )
    bid_repo.create_bid(valid_bid)
    bid_repo.create_bid(other_bid)
    _returnable(tmp_db, other_bid, "DEL-OTHER-BID")
    service, repository = _service(tmp_db, bid_repo)

    with pytest.raises(ValueError, match="belongs to a different Bid"):
        service.set_record(
            valid_bid.bid_id,
            "RETURNABLE",
            "DEL-OTHER-BID",
            {"expected_version": 0, "responsible": "Bid Manager"},
            "jason",
        )
    with pytest.raises(ValueError, match="belongs to a different Bid"):
        service.add_note(valid_bid.bid_id, "RETURNABLE", "DEL-OTHER-BID", "Update", "jason")
    with pytest.raises(ValueError, match="belongs to a different Bid"):
        service.list_notes(valid_bid.bid_id, "RETURNABLE", "DEL-OTHER-BID")

    assert repository.get_record(ExecutionSubjectKind.RETURNABLE, "DEL-OTHER-BID") is None
    assert _audit_count(tmp_db) == 0
