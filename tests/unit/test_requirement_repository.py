import io
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from core.bid_repository import BidRepository
from core.database import Database
from core.document_repository import DocumentRepository
from core.document_service import DocumentService
from core.enums import Gate, GateStatus
from core.managed_document_storage import ManagedDocumentStorage
from core.requirement_repository import REQUIREMENT_MIGRATION_ID, RequirementRepository
from core.requirements import Requirement
from core.schemas import AuditEntry, Bid, GateRecord, Provenance
from core.work_item_repository import WorkItemRepository
from core.work_item_service import WorkItemService

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)


def _record(bid: Bid) -> Requirement:
    return Requirement.model_validate(
        {
            "requirement_id": f"REQ-{UUID(int=1)}",
            "bid_id": bid.bid_id,
            "title": "Synthetic requirement",
            "statement": "Synthetic statement",
            "origin": "INTERNAL",
            "category": "OTHER",
            "significance": "SCORED",
            "lifecycle_stage": "BID",
            "lifecycle_state": "ACTIVE",
            "disposition": "UNASSESSED",
            "work_state": "OPEN",
            "review_state": "NOT_REVIEWED",
            "created_at": NOW,
            "updated_at": NOW,
            "version": 1,
            "provenance": Provenance.from_human("author"),
        }
    )


def _audit(bid: Bid) -> AuditEntry:
    return AuditEntry(
        entry_id=f"AUD-{UUID(int=2)}",
        bid_id=bid.bid_id,
        actor="author",
        action="requirement_created",
        detail='{"requirement_id":"synthetic"}',
        timestamp=NOW,
    )


def test_clean_migration_is_idempotent_and_has_expected_constraints(tmp_db: Database) -> None:
    BidRepository(tmp_db)
    DocumentRepository(tmp_db)
    first = RequirementRepository(tmp_db)
    second = RequirementRepository(tmp_db)
    assert first.list() == second.list() == []
    assert REQUIREMENT_MIGRATION_ID == "task_09_requirements_v1"
    with tmp_db._conn() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(requirements)")}
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(requirements)")}
        triggers = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'requirements'"
            )
        }
    assert {"source_document_version_id", "disposition", "review_state", "version"} <= columns
    assert "idx_requirements_source_version" in indexes
    assert "prevent_requirement_delete" in triggers


def test_direct_invalid_enum_update_and_delete_are_rejected(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    DocumentRepository(tmp_db)
    repository = RequirementRepository(tmp_db)
    item = _record(valid_bid)
    repository.create(item, _audit(valid_bid))
    with tmp_db._conn() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE requirements SET disposition = 'UNKNOWN' WHERE requirement_id = ?",
                (item.requirement_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute(
                "DELETE FROM requirements WHERE requirement_id = ?",
                (item.requirement_id,),
            )
    assert repository.get(item.requirement_id) == item
    reassigned_source = item.model_copy(
        update={
            "source_document_id": "DOC-other",
            "source_document_version_id": "DV-other",
            "updated_at": NOW,
            "version": 2,
        }
    )
    with pytest.raises(ValueError, match="cannot change fields"):
        repository.update_metadata(reassigned_source, 1, _audit(valid_bid))


def test_task08r_upgrade_preserves_existing_records(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    tmp_db.create_document({"id": "LEGACY", "filename": "legacy.pdf", "notes": "preserve"})
    bid_repo.attach_document_to_bid("LEGACY", valid_bid.bid_id)
    document_repository = DocumentRepository(tmp_db)
    document_service = DocumentService(
        document_repository,
        bid_repo,
        ManagedDocumentStorage(tmp_path / "managed", 1024),
        now_factory=lambda: NOW,
    )
    controlled, version = document_service.register_document(
        {
            "bid_id": valid_bid.bid_id,
            "title": "Controlled synthetic source",
            "category": "SOLICITATION",
            "version_label": "Original",
        },
        io.BytesIO(b"representative synthetic bytes"),
        "source.txt",
        "text/plain",
        "author",
    )
    work_repository = WorkItemRepository(tmp_db)
    work_item = WorkItemService(work_repository, bid_repo).create_work_item(
        {"bid_id": valid_bid.bid_id, "title": "Preserved TASK-07 work"},
        "author",
    )
    bid_repo.upsert_gate_record(
        GateRecord(
            bid_id=valid_bid.bid_id,
            gate=Gate.G4,
            status=GateStatus.OVERRIDDEN,
            override_by="Synthetic authority",
            override_risk_note="Preserved representative readiness evidence",
            decided_at=NOW,
        )
    )
    before_bid = bid_repo.get_bid(valid_bid.bid_id)
    before_legacy = dict(tmp_db.get_document("LEGACY"))
    before_document = document_repository.get(controlled.document_id)
    before_versions = document_repository.list_versions(controlled.document_id)
    before_work = work_repository.get(work_item.work_item_id)
    before_gate = bid_repo.get_gate_record(valid_bid.bid_id, Gate.G4)
    before_audit = bid_repo.list_audit(valid_bid.bid_id)
    RequirementRepository(tmp_db)
    RequirementRepository(tmp_db)
    assert bid_repo.get_bid(valid_bid.bid_id) == before_bid
    assert dict(tmp_db.get_document("LEGACY")) == before_legacy
    assert document_repository.get(controlled.document_id) == before_document
    assert document_repository.list_versions(controlled.document_id) == before_versions == [version]
    assert work_repository.get(work_item.work_item_id) == before_work
    assert bid_repo.get_gate_record(valid_bid.bid_id, Gate.G4) == before_gate
    assert bid_repo.list_audit(valid_bid.bid_id) == before_audit
