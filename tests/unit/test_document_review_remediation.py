import asyncio
import hashlib
import importlib.util
import io
import json
import sqlite3
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from starlette.datastructures import Headers

from core.bid_repository import BidRepository
from core.database import Database
from core.document_control import (
    ControlledDocument,
    ControlledDocumentIdentityError,
    ControlledDocumentIntegrityError,
    DocumentCategory,
    DocumentLifecycle,
    DocumentVersion,
    DocumentVersionState,
    IntegrityStatus,
    LogicalIntegrityStatus,
)
from core.document_repository import (
    DocumentRepository,
    DocumentStoreBusyError,
    StaleDocumentError,
)
from core.document_service import DocumentService
from core.managed_document_storage import (
    ManagedDocumentStorage,
    ManagedStorageFailureError,
    StorageCollisionError,
)
from core.schemas import AuditEntry, Bid, Provenance

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
FIRST = b"task-08r-synthetic-v1"
SECOND = b"task-08r-synthetic-v2"


def _document(bid: Bid, number: int, version_number: int) -> ControlledDocument:
    return ControlledDocument(
        document_id=f"DOC-{UUID(int=number)}",
        bid_id=bid.bid_id,
        title=f"Synthetic evidence {number}",
        category=DocumentCategory.SOLICITATION,
        lifecycle_state=DocumentLifecycle.ACTIVE,
        current_version_id=f"DV-{UUID(int=version_number)}",
        created_at=NOW,
        updated_at=NOW,
        version=1,
        provenance=Provenance.from_human("reviewer"),
    )


def _version(
    document: ControlledDocument,
    number: int,
    content: bytes,
    predecessor: str | None = None,
) -> DocumentVersion:
    return DocumentVersion(
        document_version_id=f"DV-{UUID(int=number)}",
        document_id=document.document_id,
        version_label=f"Version {number}",
        original_filename="synthetic.txt",
        media_type="text/plain",
        byte_size=len(content),
        sha256_digest=hashlib.sha256(content).hexdigest(),
        storage_key=f"versions/{number:02x}/DV-{UUID(int=number)}.bin",
        predecessor_version_id=predecessor,
        version_state=DocumentVersionState.CURRENT,
        created_at=NOW,
        provenance=Provenance.from_human("reviewer"),
    )


def _audit(document: ControlledDocument, number: int) -> AuditEntry:
    return AuditEntry(
        entry_id=f"AUD-{UUID(int=number)}",
        bid_id=document.bid_id,
        actor="reviewer",
        action="task_08r_test",
        detail=json.dumps({"document_id": document.document_id}),
        timestamp=NOW,
    )


def _seed(
    repository: DocumentRepository,
    bid: Bid,
    document_number: int,
    version_number: int,
) -> tuple[ControlledDocument, DocumentVersion]:
    document = _document(bid, document_number, version_number)
    version = _version(document, version_number, FIRST + bytes([document_number]))
    repository.create_with_first_version(
        document,
        version,
        _audit(document, document_number),
    )
    return document, version


def _service(
    db: Database,
    bids: BidRepository,
    root: Path,
    first_id: int,
) -> DocumentService:
    ids = iter(range(first_id, first_id + 100))
    return DocumentService(
        DocumentRepository(db),
        bids,
        ManagedDocumentStorage(root, 4096),
        now_factory=lambda: NOW,
        id_factory=lambda: UUID(int=next(ids)),
    )


def _register(service: DocumentService, bid: Bid) -> tuple[ControlledDocument, DocumentVersion]:
    return service.register_document(
        {
            "bid_id": bid.bid_id,
            "title": "Synthetic controlled record",
            "category": "SOLICITATION",
            "version_label": "Original",
        },
        io.BytesIO(FIRST),
        "synthetic.txt",
        "text/plain",
        "reviewer",
    )


def test_migration_is_self_contained_idempotent_and_failure_is_transactional(
    tmp_path: Path,
) -> None:
    direct_db = Database(tmp_path / "direct.db")
    first = DocumentRepository(direct_db)
    second = DocumentRepository(direct_db)
    assert first.list_documents() == second.list_documents() == []

    failed_db = Database(tmp_path / "failed.db")
    with failed_db._conn() as conn:
        before = {str(row["name"]) for row in conn.execute("PRAGMA table_info(documents)")}
        conn.execute("CREATE TABLE document_versions (broken TEXT)")
    with pytest.raises(sqlite3.OperationalError, match="document_id"):
        DocumentRepository(failed_db)
    with failed_db._conn() as conn:
        after = {str(row["name"]) for row in conn.execute("PRAGMA table_info(documents)")}
        conn.execute("DROP TABLE document_versions")
    assert after == before
    assert "control_managed" not in after
    assert DocumentRepository(failed_db).list_documents() == []


def test_controlled_identity_is_locked_and_legacy_attachment_still_works(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    repository = DocumentRepository(tmp_db)
    document, _ = _seed(repository, valid_bid, 1, 2)
    mutations = (
        ("UPDATE documents SET control_managed = 0 WHERE id = ?", (document.document_id,)),
        ("UPDATE documents SET bid_id = NULL WHERE id = ?", (document.document_id,)),
        ("UPDATE documents SET bid_id = 'B-other' WHERE id = ?", (document.document_id,)),
    )
    for sql, values in mutations:
        with tmp_db._conn() as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql, values)
    with pytest.raises(ControlledDocumentIdentityError):
        bid_repo.detach_document(document.document_id)
    with pytest.raises(ControlledDocumentIdentityError):
        bid_repo.attach_document_to_bid(document.document_id, valid_bid.bid_id)
    tmp_db.create_document({"id": "LEGACY", "filename": "legacy.txt"})
    bid_repo.attach_document_to_bid("LEGACY", valid_bid.bid_id)
    assert tmp_db.get_document("LEGACY")["bid_id"] == valid_bid.bid_id
    with tmp_db._conn() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE documents SET control_managed = 1 WHERE id = 'LEGACY'")
    assert repository.get(document.document_id) == document
    assert len(bid_repo.list_audit(valid_bid.bid_id)) == 1


def test_null_controlled_bid_decodes_as_precise_integrity_failure(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    repository = DocumentRepository(tmp_db)
    document, _ = _seed(repository, valid_bid, 1, 2)
    with tmp_db._conn() as conn:
        conn.execute("DROP TRIGGER validate_controlled_document_update")
        conn.execute("UPDATE documents SET bid_id = NULL WHERE id = ?", (document.document_id,))
    repository = DocumentRepository(tmp_db)
    with pytest.raises(ControlledDocumentIntegrityError, match="missing bid ownership"):
        repository.get(document.document_id)
    issues = repository.diagnose_logical_integrity()
    assert LogicalIntegrityStatus.IDENTITY_CORRUPT in {issue.status for issue in issues}
    entry = repository.list_register_entries()[0]
    assert entry.bid_id is None
    assert entry.document is None


def test_version_evidence_is_immutable_and_delete_is_blocked(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    repository = DocumentRepository(tmp_db)
    document, version = _seed(repository, valid_bid, 1, 2)
    immutable_updates = (
        "document_version_id = 'DV-other'",
        "document_id = 'DOC-other'",
        "predecessor_version_id = document_version_id",
        "version_label = 'changed'",
        "original_filename = 'changed.txt'",
        "media_type = 'application/pdf'",
        "byte_size = byte_size + 1",
        f"sha256_digest = '{'0' * 64}'",
        "storage_key = 'versions/ff/changed.bin'",
        "created_at = '2027-01-01T00:00:00+00:00'",
        "provenance_json = '{}'",
        "version_state = 'SUPERSEDED'",
        "version_state = 'SUPERSEDED', version_label = 'changed'",
    )
    for assignment in immutable_updates:
        with tmp_db._conn() as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"UPDATE document_versions SET {assignment} WHERE document_version_id = ?",
                (version.document_version_id,),
            )
    with tmp_db._conn() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "DELETE FROM document_versions WHERE document_version_id = ?",
            (version.document_version_id,),
        )
    assert repository.get_version(version.document_version_id) == version
    assert repository.get(document.document_id) == document


def test_supported_successor_is_only_state_transition_and_repository_asserts_inputs(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    repository = DocumentRepository(tmp_db)
    document, first = _seed(repository, valid_bid, 1, 2)
    bad = _version(document, 3, SECOND, first.document_version_id).model_copy(
        update={"version_state": DocumentVersionState.SUPERSEDED}
    )
    updated = document.model_copy(
        update={"current_version_id": bad.document_version_id, "version": 2}
    )
    with pytest.raises(ValueError, match="CURRENT"):
        repository.add_version(updated, bad, 1, first.document_version_id, _audit(updated, 3))
    second = bad.model_copy(update={"version_state": DocumentVersionState.CURRENT})
    repository.add_version(updated, second, 1, first.document_version_id, _audit(updated, 4))
    history = repository.list_versions(document.document_id)
    assert [item.version_state for item in history] == [
        DocumentVersionState.CURRENT,
        DocumentVersionState.SUPERSEDED,
    ]
    assert history[0].predecessor_version_id == history[1].document_version_id
    with tmp_db._conn() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE document_versions SET version_state = 'CURRENT' WHERE document_version_id = ?",
            (first.document_version_id,),
        )


def test_withdrawal_blocks_versions_but_allows_audited_descriptive_correction(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    service = _service(tmp_db, bid_repo, tmp_path / "managed", 1)
    document, version = _register(service, valid_bid)
    withdrawn = service.withdraw(document.document_id, document.version, "reviewer")
    keys = list(service.storage.iter_managed_keys())
    audit_before = list(bid_repo.list_audit(valid_bid.bid_id))
    candidate = _version(withdrawn, 50, SECOND, version.document_version_id)
    proposed = withdrawn.model_copy(
        update={"current_version_id": candidate.document_version_id, "version": 3}
    )
    with pytest.raises(ValueError, match="withdrawn"):
        service.repository.add_version(
            proposed,
            candidate,
            withdrawn.version,
            version.document_version_id,
            _audit(proposed, 50),
        )
    with pytest.raises(ValueError, match="withdrawn"):
        service.add_version(
            document.document_id,
            {
                "version_label": "Rejected",
                "expected_document_version": withdrawn.version,
                "expected_current_version_id": version.document_version_id,
            },
            io.BytesIO(SECOND),
            "rejected.txt",
            "text/plain",
            "reviewer",
        )
    corrected = service.update_metadata(
        document.document_id,
        {"expected_version": withdrawn.version, "title": "Corrected historic title"},
        "reviewer",
    )
    assert corrected.lifecycle_state == DocumentLifecycle.WITHDRAWN
    assert corrected.bid_id == document.bid_id
    assert corrected.current_version_id == version.document_version_id
    assert service.list_versions(document.document_id) == [version]
    assert list(service.storage.iter_managed_keys()) == keys
    assert service.storage.staging_files() == []
    assert len(bid_repo.list_audit(valid_bid.bid_id)) == len(audit_before) + 1


def test_logical_diagnostics_report_corruption_without_repair(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    repository = DocumentRepository(tmp_db)
    zero_doc, zero_version = _seed(repository, valid_bid, 1, 11)
    pointer_doc, pointer_version = _seed(repository, valid_bid, 2, 12)
    missing_doc, missing_version = _seed(repository, valid_bid, 3, 13)
    cross_doc, cross_version = _seed(repository, valid_bid, 4, 14)
    cycle_doc, cycle_first = _seed(repository, valid_bid, 5, 15)
    predecessor_doc, predecessor_version = _seed(repository, valid_bid, 6, 17)
    no_pointer_doc, _ = _seed(repository, valid_bid, 7, 18)
    cycle_second = _version(cycle_doc, 16, SECOND, cycle_first.document_version_id)
    cycle_updated = cycle_doc.model_copy(
        update={"current_version_id": cycle_second.document_version_id, "version": 2}
    )
    repository.add_version(
        cycle_updated,
        cycle_second,
        1,
        cycle_first.document_version_id,
        _audit(cycle_updated, 20),
    )
    with tmp_db._conn() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TRIGGER enforce_document_version_immutability")
        conn.execute("DROP TRIGGER validate_controlled_document_update")
        conn.execute(
            "UPDATE document_versions SET version_state = 'SUPERSEDED' "
            "WHERE document_version_id = ?",
            (zero_version.document_version_id,),
        )
        conn.execute(
            "UPDATE documents SET current_version_id = ? WHERE id = ?",
            (zero_version.document_version_id, pointer_doc.document_id),
        )
        conn.execute(
            "UPDATE documents SET current_version_id = 'DV-missing' WHERE id = ?",
            (missing_doc.document_id,),
        )
        conn.execute(
            "UPDATE document_versions SET predecessor_version_id = ? WHERE document_version_id = ?",
            (pointer_version.document_version_id, cross_version.document_version_id),
        )
        conn.execute(
            "UPDATE document_versions SET predecessor_version_id = ? WHERE document_version_id = ?",
            (cycle_second.document_version_id, cycle_first.document_version_id),
        )
        conn.execute(
            "UPDATE document_versions SET predecessor_version_id = 'DV-missing-predecessor' "
            "WHERE document_version_id = ?",
            (predecessor_version.document_version_id,),
        )
        conn.execute(
            "UPDATE documents SET current_version_id = NULL WHERE id = ?",
            (no_pointer_doc.document_id,),
        )
    repository = DocumentRepository(tmp_db)
    database_path = Path(tmp_db.db_path)
    before = database_path.read_bytes()
    issues = repository.diagnose_logical_integrity()
    statuses = {(issue.document_id, issue.status) for issue in issues}
    assert (zero_doc.document_id, LogicalIntegrityStatus.CURRENT_COUNT_INVALID) in statuses
    assert (pointer_doc.document_id, LogicalIntegrityStatus.POINTER_CROSS_DOCUMENT) in statuses
    assert (missing_doc.document_id, LogicalIntegrityStatus.POINTER_MISSING) in statuses
    assert (cross_doc.document_id, LogicalIntegrityStatus.LINEAGE_CROSS_DOCUMENT) in statuses
    assert (cycle_doc.document_id, LogicalIntegrityStatus.LINEAGE_CYCLE) in statuses
    assert (
        predecessor_doc.document_id,
        LogicalIntegrityStatus.LINEAGE_MISSING_PREDECESSOR,
    ) in statuses
    assert (
        no_pointer_doc.document_id,
        LogicalIntegrityStatus.MISSING_CURRENT_POINTER,
    ) in statuses
    assert database_path.read_bytes() == before
    assert missing_version.document_version_id != "DV-missing"


def test_symlink_verification_and_diagnostic_never_follow_target(
    tmp_path: Path,
    valid_bid: Bid,
) -> None:
    root = tmp_path / "managed"
    storage = ManagedDocumentStorage(root, 1024)
    outside = tmp_path / "outside-secret.bin"
    outside.write_bytes(b"external-content")
    version = _version(_document(valid_bid, 1, 2), 2, b"external-content")
    link = storage._raw_path(version.storage_key)
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    before = outside.read_bytes()
    result = storage.verify(version)
    assert result.status == IntegrityStatus.UNREADABLE
    assert "symbolic link" in result.reason
    assert list(storage.iter_symlink_keys()) == [version.storage_key]
    assert list(storage.iter_managed_keys()) == []
    assert outside.read_bytes() == before


def test_versions_root_symlink_is_reported_without_traversal(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    storage = ManagedDocumentStorage(root, 1024)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "must-not-enumerate.bin").write_bytes(b"external")
    (root / "versions").symlink_to(outside, target_is_directory=True)
    assert list(storage.iter_symlink_keys()) == ["versions"]
    assert list(storage.iter_managed_keys()) == []


def test_two_successor_writers_leave_one_current_and_no_file_or_audit_orphans(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    root = tmp_path / "managed"
    initial = _service(tmp_db, bid_repo, root, 1)
    document, first = _register(initial, valid_bid)
    services = [_service(tmp_db, bid_repo, root, 100), _service(tmp_db, bid_repo, root, 200)]
    barrier = threading.Barrier(2)
    for service in services:
        original = service.repository.add_version

        def synchronized(
            *args: object,
            _original: Callable[..., None] = original,
            **kwargs: object,
        ) -> None:
            barrier.wait(timeout=5)
            _original(*args, **kwargs)

        service.repository.add_version = synchronized  # type: ignore[method-assign]
    results: list[str] = []
    failures: list[Exception] = []

    def write(service: DocumentService, label: str, content: bytes) -> None:
        try:
            _, version = service.add_version(
                document.document_id,
                {
                    "version_label": label,
                    "expected_document_version": document.version,
                    "expected_current_version_id": first.document_version_id,
                },
                io.BytesIO(content),
                f"{label}.txt",
                "text/plain",
                "reviewer",
            )
            results.append(version.document_version_id)
        except Exception as exc:  # noqa: BLE001 - asserted controlled below
            failures.append(exc)

    threads = [
        threading.Thread(target=write, args=(services[0], "one", SECOND)),
        threading.Thread(target=write, args=(services[1], "two", SECOND + b"-other")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == len(failures) == 1
    assert isinstance(failures[0], (StaleDocumentError, DocumentStoreBusyError))
    history = initial.list_versions(document.document_id)
    assert sum(item.version_state == DocumentVersionState.CURRENT for item in history) == 1
    assert history[0].predecessor_version_id == first.document_version_id
    assert initial.get_document(document.document_id).current_version_id == results[0]
    assert len(bid_repo.list_audit(valid_bid.bid_id)) == 2
    assert len(list(initial.storage.iter_managed_keys())) == 2
    assert initial.storage.staging_files() == []


@pytest.fixture
def remediation_ui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setenv("CONTRACTIQ_DOCUMENT_ROOT", str(tmp_path / "managed"))
    sys.modules.pop("app", None)
    app_path = Path(__file__).parents[2] / "app.py"
    spec = importlib.util.spec_from_file_location("app", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load isolated application")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    return module


def _upload(content: bytes) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(content),
        filename="synthetic.txt",
        headers=Headers({"content-type": "text/plain"}),
    )


def test_ui_degrades_one_corrupt_row_and_error_taxonomy_is_safe(
    remediation_ui_app: ModuleType,
    valid_bid: Bid,
) -> None:
    app = remediation_ui_app
    app.bid_repository.create_bid(valid_bid)
    created: list[dict[str, object]] = []
    for title in ("Valid row", "Corrupt row"):
        response = asyncio.run(
            app.register_controlled_document(
                file=_upload(title.encode()),
                bid_id=valid_bid.bid_id,
                title=title,
                category="SOLICITATION",
                version_label="Original",
                document_number=None,
                issuer=None,
                notes=None,
                issued_date=None,
                received_at=None,
                actor="reviewer",
            )
        )
        created.append(cast(dict[str, object], json.loads(bytes(response.body))))
    corrupt = cast(dict[str, object], created[1]["document"])
    with app.db._conn() as conn:
        conn.execute("DROP TRIGGER validate_controlled_document_update")
        conn.execute(
            "UPDATE documents SET bid_id = NULL WHERE id = ?",
            (str(corrupt["document_id"]),),
        )
    app.document_repository = DocumentRepository(app.db)
    app.document_service.repository = app.document_repository
    page = asyncio.run(app.controlled_documents(cast(Request, object()), None, None, None))
    text = bytes(cast(HTMLResponse, page).body).decode()
    assert "Valid row" in text
    assert "Corrupt row" in text
    assert "CONTROL INTEGRITY ISSUE" in text
    assert str(app.MANAGED_DOCUMENT_ROOT) not in text
    assert "versions/" not in text

    cases = (
        (StaleDocumentError("stale"), 409, "stale"),
        (DocumentStoreBusyError("/private/db is locked"), 503, "temporarily busy"),
        (ManagedStorageFailureError("/private/managed failed"), 500, "could not complete"),
        (StorageCollisionError("/private/collision"), 500, "could not complete"),
        (OSError("/private/disk"), 503, "temporarily unavailable"),
    )
    for error, status, detail in cases:
        mapped = app._mutation_error(error)
        assert isinstance(mapped, HTTPException)
        assert mapped.status_code == status
        assert detail in str(mapped.detail)
        assert "/private/" not in str(mapped.detail)

    def collide(*args: object, **kwargs: object) -> Path:
        raise StorageCollisionError("/private/impossible-collision")

    app.document_service.storage.place = collide
    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            app.register_controlled_document(
                file=_upload(b"collision"),
                bid_id=valid_bid.bid_id,
                title="Collision route proof",
                category="OTHER",
                version_label="Original",
                document_number=None,
                issuer=None,
                notes=None,
                issued_date=None,
                received_at=None,
                actor="reviewer",
            )
        )
    assert raised.value.status_code == 500
    assert "/private/" not in str(raised.value.detail)
    assert app.document_service.storage.staging_files() == []
