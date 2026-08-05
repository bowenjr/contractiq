import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from core.bid_repository import BidRepository
from core.database import Database
from core.document_control import (
    ControlledDocument,
    DocumentCategory,
    DocumentLifecycle,
    DocumentVersion,
    DocumentVersionState,
)
from core.document_repository import (
    DOCUMENT_CONTROL_MIGRATION_ID,
    DocumentRepository,
    StaleDocumentError,
)
from core.enums import Gate, GateStatus
from core.schemas import AuditEntry, Bid, GateRecord, Provenance
from core.work_item_repository import WorkItemRepository
from core.work_item_service import WorkItemService

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
CONTENT_1 = b"synthetic-version-one"
CONTENT_2 = b"synthetic-version-two"


def _document(bid: Bid, *, doc_num: int = 1, version_num: int = 2) -> ControlledDocument:
    return ControlledDocument(
        document_id=f"DOC-{UUID(int=doc_num)}",
        bid_id=bid.bid_id,
        title="Synthetic RFP",
        category=DocumentCategory.SOLICITATION,
        lifecycle_state=DocumentLifecycle.ACTIVE,
        current_version_id=f"DV-{UUID(int=version_num)}",
        created_at=NOW,
        updated_at=NOW,
        version=1,
        provenance=Provenance.from_human("jason"),
    )


def _version(
    document: ControlledDocument,
    content: bytes,
    *,
    version_num: int = 2,
    predecessor: str | None = None,
) -> DocumentVersion:
    return DocumentVersion(
        document_version_id=f"DV-{UUID(int=version_num)}",
        document_id=document.document_id,
        version_label="Original" if predecessor is None else "Rev 1",
        original_filename="synthetic.txt",
        media_type="text/plain",
        byte_size=len(content),
        sha256_digest=hashlib.sha256(content).hexdigest(),
        storage_key=f"versions/00/DV-{UUID(int=version_num)}.bin",
        predecessor_version_id=predecessor,
        version_state=DocumentVersionState.CURRENT,
        created_at=NOW,
        provenance=Provenance.from_human("jason"),
    )


def _audit(
    document: ControlledDocument, entry_id: str, action: str = "document_test"
) -> AuditEntry:
    return AuditEntry(
        entry_id=entry_id,
        bid_id=document.bid_id,
        actor="jason",
        action=action,
        detail=f'{{"document_id":"{document.document_id}"}}',
        timestamp=NOW,
    )


def test_migration_succeeds_on_new_database(tmp_path: Path) -> None:
    db = Database(tmp_path / "new.db")
    BidRepository(db)
    DocumentRepository(db)
    with db._conn() as conn:
        document_columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
        version_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(document_versions)")
        }
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(document_versions)")}
        foreign_keys = conn.execute("PRAGMA foreign_key_list(document_versions)").fetchall()
    assert DOCUMENT_CONTROL_MIGRATION_ID == "task_08_document_control_v1"
    assert {"control_managed", "control_title", "current_version_id", "control_version"} <= (
        document_columns
    )
    assert {"sha256_digest", "storage_key", "predecessor_version_id"} <= version_columns
    assert "uq_document_versions_current" in indexes
    assert {row["table"] for row in foreign_keys} == {"documents", "document_versions"}


def test_task07_upgrade_preserves_bid_legacy_document_work_item_and_audit(
    tmp_path: Path,
    valid_bid: Bid,
) -> None:
    db = Database(tmp_path / "task07.db")
    bids = BidRepository(db)
    bids.create_bid(valid_bid)
    db.create_document({"id": "LEGACY-DOC", "filename": "legacy.pdf", "notes": "keep"})
    bids.attach_document_to_bid("LEGACY-DOC", valid_bid.bid_id)
    work = WorkItemService(WorkItemRepository(db), bids)
    item = work.create_work_item({"bid_id": valid_bid.bid_id, "title": "Keep work"}, "jason")
    bids.upsert_gate_record(
        GateRecord(
            bid_id=valid_bid.bid_id,
            gate=Gate.G4,
            status=GateStatus.OVERRIDDEN,
            override_by="Executive Sponsor",
            override_risk_note="Synthetic TASK-07 readiness evidence",
            decided_at=NOW,
        )
    )
    baseline_audit = list(bids.list_audit(valid_bid.bid_id))

    first = DocumentRepository(db)
    second = DocumentRepository(db)

    assert first.list_documents() == second.list_documents() == []
    assert bids.get_bid(valid_bid.bid_id) == valid_bid
    assert db.get_document("LEGACY-DOC")["notes"] == "keep"
    assert WorkItemRepository(db).get(item.work_item_id) == item
    gate = bids.get_gate_record(valid_bid.bid_id, Gate.G4)
    assert gate is not None and gate.status == GateStatus.OVERRIDDEN
    assert bids.list_audit(valid_bid.bid_id) == baseline_audit


def test_create_first_version_writes_current_pointer_and_audit(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    repository = DocumentRepository(tmp_db)
    document = _document(valid_bid)
    version = _version(document, CONTENT_1)
    repository.create_with_first_version(document, version, _audit(document, "AUD-1"))
    assert repository.get(document.document_id) == document
    assert repository.list_versions(document.document_id) == [version]
    assert bid_repo.list_audit(valid_bid.bid_id)[0].entry_id == "AUD-1"


def test_create_cannot_commit_without_first_version(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    repository = DocumentRepository(tmp_db)
    assert not hasattr(repository, "create_document")
    assert not hasattr(repository, "delete")
    with tmp_db._conn() as conn, pytest.raises(sqlite3.IntegrityError):
        document = _document(valid_bid)
        conn.execute(
            """
            INSERT INTO documents (
                id, bid_id, filename, control_managed, control_title,
                document_category, control_lifecycle, current_version_id,
                control_version
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                document.document_id,
                document.bid_id,
                "x",
                1,
                document.title,
                document.category.value,
                document.lifecycle_state.value,
                None,
                1,
            ),
        )


def test_add_version_supersedes_exact_predecessor_and_keeps_one_current(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    repository = DocumentRepository(tmp_db)
    document = _document(valid_bid)
    first = _version(document, CONTENT_1)
    repository.create_with_first_version(document, first, _audit(document, "AUD-1"))
    second = _version(document, CONTENT_2, version_num=3, predecessor=first.document_version_id)
    updated = document.model_copy(
        update={"current_version_id": second.document_version_id, "version": 2}
    )
    repository.add_version(
        updated,
        second,
        1,
        first.document_version_id,
        _audit(updated, "AUD-2"),
    )
    history = repository.list_versions(document.document_id)
    assert history[0] == second
    assert history[1].version_state == DocumentVersionState.SUPERSEDED
    assert history[1].document_version_id == first.document_version_id
    assert sum(version.version_state == DocumentVersionState.CURRENT for version in history) == 1
    assert [entry.entry_id for entry in bid_repo.list_audit(valid_bid.bid_id)] == [
        "AUD-1",
        "AUD-2",
    ]


def test_cross_document_lineage_is_rejected_by_repository(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    repository = DocumentRepository(tmp_db)
    one = _document(valid_bid, doc_num=1, version_num=2)
    two = _document(valid_bid, doc_num=3, version_num=4)
    repository.create_with_first_version(
        one, _version(one, CONTENT_1, version_num=2), _audit(one, "A1")
    )
    repository.create_with_first_version(
        two, _version(two, CONTENT_1, version_num=4), _audit(two, "A2")
    )
    candidate = _version(two, CONTENT_2, version_num=5, predecessor=one.current_version_id)
    updated = two.model_copy(
        update={"current_version_id": candidate.document_version_id, "version": 2}
    )
    with pytest.raises(StaleDocumentError):
        repository.add_version(updated, candidate, 1, one.current_version_id, _audit(two, "A3"))
    assert len(repository.list_versions(two.document_id)) == 1


def test_metadata_and_withdrawal_are_audited_and_history_is_preserved(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    repository = DocumentRepository(tmp_db)
    document = _document(valid_bid)
    version = _version(document, CONTENT_1)
    repository.create_with_first_version(document, version, _audit(document, "A1"))
    edited = document.model_copy(update={"title": "Edited", "version": 2})
    repository.update_metadata(edited, 1, _audit(edited, "A2"))
    withdrawn = edited.model_copy(
        update={"lifecycle_state": DocumentLifecycle.WITHDRAWN, "version": 3}
    )
    repository.withdraw(withdrawn, 2, _audit(withdrawn, "A3"))
    assert repository.get(document.document_id) == withdrawn
    assert repository.list_versions(document.document_id) == [version]
    assert len(bid_repo.list_audit(valid_bid.bid_id)) == 3


def test_audit_failure_rolls_back_authoritative_create_and_update(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    repository = DocumentRepository(tmp_db)
    seed = _audit(_document(valid_bid), "DUPLICATE")
    bid_repo.append_audit(seed)
    document = _document(valid_bid)
    with pytest.raises(sqlite3.IntegrityError):
        repository.create_with_first_version(
            document,
            _version(document, CONTENT_1),
            _audit(document, "DUPLICATE"),
        )
    assert repository.get(document.document_id) is None
    assert repository.list_versions(document.document_id) == []
    assert bid_repo.list_audit(valid_bid.bid_id) == [seed]


def test_stale_update_leaves_no_orphan_audit(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    repository = DocumentRepository(tmp_db)
    document = _document(valid_bid)
    repository.create_with_first_version(
        document, _version(document, CONTENT_1), _audit(document, "A1")
    )
    stale = document.model_copy(update={"title": "Stale", "version": 2})
    with pytest.raises(StaleDocumentError):
        repository.update_metadata(stale, 99, _audit(stale, "ORPHAN"))
    assert [entry.entry_id for entry in bid_repo.list_audit(valid_bid.bid_id)] == ["A1"]
