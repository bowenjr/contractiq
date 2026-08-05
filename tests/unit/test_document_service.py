import io
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from core.bid_repository import BidRepository
from core.database import Database
from core.document_control import (
    DocumentCategory,
    DocumentLifecycle,
    DocumentVersionState,
    IntegrityStatus,
)
from core.document_repository import (
    DocumentRepository,
    DuplicateDocumentVersionError,
    StaleDocumentError,
)
from core.document_service import DocumentService
from core.managed_document_storage import (
    EmptyManagedFileError,
    ManagedDocumentStorage,
    ManagedFileTooLargeError,
)
from core.schemas import AuditEntry, Bid

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
FIRST = b"synthetic solicitation v1"
SECOND = b"synthetic solicitation v2"


def _service(
    tmp_path: Path,
    db: Database,
    bids: BidRepository,
    *,
    max_bytes: int = 1024,
) -> DocumentService:
    ids = iter(range(1, 100))
    return DocumentService(
        DocumentRepository(db),
        bids,
        ManagedDocumentStorage(tmp_path / "managed", max_bytes),
        now_factory=lambda: NOW,
        id_factory=lambda: UUID(int=next(ids)),
    )


def _register(service: DocumentService, bid: Bid, content: bytes = FIRST):
    return service.register_document(
        {
            "bid_id": bid.bid_id,
            "title": "Synthetic RFP",
            "document_number": "RFP-001",
            "category": "SOLICITATION",
            "issuer": "Example EPC",
            "version_label": "Original",
            "issued_date": "2026-08-01",
        },
        io.BytesIO(content),
        "../../Synthetic RFP.txt",
        "text/plain",
        "jason",
    )


def test_register_requires_existing_bid_before_file_mutation(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
) -> None:
    service = _service(tmp_path, tmp_db, bid_repo)
    with pytest.raises(ValueError, match="Bid not found"):
        service.register_document(
            {
                "bid_id": "B-2026-9999",
                "title": "RFP",
                "category": "SOLICITATION",
                "version_label": "Original",
            },
            io.BytesIO(FIRST),
            "rfp.txt",
            "text/plain",
            "jason",
        )
    assert service.storage.staging_files() == []
    assert list(service.storage.iter_managed_keys()) == []


def test_register_streams_first_version_and_safe_download(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    service = _service(tmp_path, tmp_db, bid_repo)
    document, version = _register(service, valid_bid)
    assert document.current_version_id == version.document_version_id
    assert version.original_filename == "Synthetic RFP.txt"
    assert "Synthetic RFP" not in version.storage_key
    assert not Path(version.storage_key).is_absolute()
    downloaded, stream = service.open_download(version.document_version_id)
    with stream:
        assert stream.read() == FIRST
    assert downloaded == version
    assert service.verify_integrity(version.document_version_id).status == IntegrityStatus.OK


def test_add_version_enforces_lineage_one_current_and_duplicate_no_mutation(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    service = _service(tmp_path, tmp_db, bid_repo)
    document, first = _register(service, valid_bid)
    updated, second = service.add_version(
        document.document_id,
        {
            "version_label": "Addendum 1 incorporated",
            "expected_document_version": document.version,
            "expected_current_version_id": first.document_version_id,
        },
        io.BytesIO(SECOND),
        "rev2.txt",
        "text/plain",
        "jason",
    )
    history = service.list_versions(document.document_id)
    assert updated.current_version_id == second.document_version_id
    assert second.predecessor_version_id == first.document_version_id
    assert [version.version_state for version in history] == [
        DocumentVersionState.CURRENT,
        DocumentVersionState.SUPERSEDED,
    ]
    before_keys = list(service.storage.iter_managed_keys())
    before_audit = list(bid_repo.list_audit(valid_bid.bid_id))
    with pytest.raises(DuplicateDocumentVersionError):
        service.add_version(
            document.document_id,
            {
                "version_label": "Duplicate",
                "expected_document_version": updated.version,
                "expected_current_version_id": second.document_version_id,
            },
            io.BytesIO(SECOND),
            "duplicate.txt",
            None,
            "jason",
        )
    assert list(service.storage.iter_managed_keys()) == before_keys
    assert bid_repo.list_audit(valid_bid.bid_id) == before_audit
    assert service.storage.staging_files() == []


def test_stale_version_expectation_is_rejected_before_staging(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    service = _service(tmp_path, tmp_db, bid_repo)
    document, first = _register(service, valid_bid)
    with pytest.raises(StaleDocumentError):
        service.add_version(
            document.document_id,
            {
                "version_label": "Rev 1",
                "expected_document_version": 99,
                "expected_current_version_id": first.document_version_id,
            },
            io.BytesIO(SECOND),
            "rev.txt",
            None,
            "jason",
        )
    assert service.storage.staging_files() == []


def test_empty_oversized_and_invalid_inputs_leave_no_authoritative_mutation(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    service = _service(tmp_path, tmp_db, bid_repo, max_bytes=4)
    base = {
        "bid_id": valid_bid.bid_id,
        "title": "RFP",
        "category": "SOLICITATION",
        "version_label": "Original",
    }
    with pytest.raises(EmptyManagedFileError):
        service.register_document(base, io.BytesIO(b""), "x", None, "jason")
    with pytest.raises(ManagedFileTooLargeError):
        service.register_document(base, io.BytesIO(b"12345"), "x", None, "jason")
    with pytest.raises(ValidationError):
        service.register_document({**base, "title": " "}, io.BytesIO(b"1234"), "x", None, "jason")
    assert service.repository.list_documents() == []
    assert bid_repo.list_audit(valid_bid.bid_id) == []
    assert service.storage.staging_files() == []
    assert list(service.storage.iter_managed_keys()) == []


def test_database_failure_compensates_newly_placed_file(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bid_repo.create_bid(valid_bid)
    service = _service(tmp_path, tmp_db, bid_repo)

    def fail(*args: object, **kwargs: object) -> None:
        raise sqlite3.IntegrityError("induced audit/database failure")

    monkeypatch.setattr(service.repository, "create_with_first_version", fail)
    with pytest.raises(sqlite3.IntegrityError, match="induced"):
        _register(service, valid_bid)
    assert service.repository.list_documents() == []
    assert bid_repo.list_audit(valid_bid.bid_id) == []
    assert service.storage.staging_files() == []
    assert list(service.storage.iter_managed_keys()) == []


def test_add_version_audit_failure_rolls_back_lineage_and_removes_file(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    service = _service(tmp_path, tmp_db, bid_repo)
    document, first = _register(service, valid_bid)
    duplicate_audit_id = f"AUD-{UUID(int=5)}"
    bid_repo.append_audit(
        AuditEntry(
            entry_id=duplicate_audit_id,
            bid_id=valid_bid.bid_id,
            actor="test",
            action="collision_seed",
            detail="synthetic audit collision",
            timestamp=NOW,
        )
    )
    keys_before = list(service.storage.iter_managed_keys())
    with pytest.raises(sqlite3.IntegrityError):
        service.add_version(
            document.document_id,
            {
                "version_label": "Rev 1",
                "expected_document_version": document.version,
                "expected_current_version_id": first.document_version_id,
            },
            io.BytesIO(SECOND),
            "rev.txt",
            "text/plain",
            "jason",
        )
    persisted = service.get_document(document.document_id)
    history = service.list_versions(document.document_id)
    assert persisted == document
    assert history == [first]
    assert history[0].version_state == DocumentVersionState.CURRENT
    assert list(service.storage.iter_managed_keys()) == keys_before
    assert service.storage.staging_files() == []


def test_file_placement_failure_leaves_no_database_or_audit(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bid_repo.create_bid(valid_bid)
    service = _service(tmp_path, tmp_db, bid_repo)

    def fail_place(*args: object, **kwargs: object) -> Path:
        raise OSError("induced placement failure")

    monkeypatch.setattr(service.storage, "place", fail_place)
    with pytest.raises(OSError, match="placement"):
        _register(service, valid_bid)
    assert service.repository.list_documents() == []
    assert bid_repo.list_audit(valid_bid.bid_id) == []
    assert service.storage.staging_files() == []


def test_metadata_update_and_withdrawal_preserve_history(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    service = _service(tmp_path, tmp_db, bid_repo)
    document, version = _register(service, valid_bid)
    edited = service.update_metadata(
        document.document_id,
        {
            "expected_version": document.version,
            "title": "Synthetic RFP revised title",
            "notes": "Controlled copy",
            "category": DocumentCategory.CONTRACTUAL,
        },
        "jason",
    )
    withdrawn = service.withdraw(document.document_id, edited.version, "jason")
    assert withdrawn.lifecycle_state == DocumentLifecycle.WITHDRAWN
    assert service.list_versions(document.document_id) == [version]
    assert service.list_documents(lifecycle="WITHDRAWN") == [withdrawn]
    assert len(bid_repo.list_audit(valid_bid.bid_id)) == 3
    assert not hasattr(service, "delete_document")


def test_integrity_mismatches_are_read_only_and_diagnostic_separates_orphans(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    service = _service(tmp_path, tmp_db, bid_repo)
    document, version = _register(service, valid_bid)
    before_document = service.get_document(document.document_id)
    before_audit = list(bid_repo.list_audit(valid_bid.bid_id))
    managed_path = service.storage.resolve(version.storage_key)
    managed_path.write_bytes(b"X" * len(FIRST))
    first_result = service.verify_integrity(version.document_version_id)
    second_result = service.verify_integrity(version.document_version_id)
    assert first_result == second_result
    assert first_result.status == IntegrityStatus.HASH_MISMATCH
    orphan = service.storage.resolve("versions/ff/orphan.bin")
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"orphan")
    diagnostic = service.diagnose_storage()
    assert diagnostic.committed_file_results == [first_result]
    assert diagnostic.unreferenced_storage_keys == ["versions/ff/orphan.bin"]
    assert service.get_document(document.document_id) == before_document
    assert bid_repo.list_audit(valid_bid.bid_id) == before_audit
