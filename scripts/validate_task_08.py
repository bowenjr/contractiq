"""Deterministic TASK-08 acceptance validation using synthetic local evidence only."""

import hashlib
import io
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from pydantic import ValidationError

from core.bid_repository import BidRepository
from core.database import Database
from core.document_control import (
    ControlledDocument,
    DocumentVersion,
    DocumentVersionState,
    IntegrityStatus,
)
from core.document_repository import (
    DocumentRepository,
    DuplicateDocumentVersionError,
)
from core.document_service import DocumentService
from core.enums import BidLevel, CustomerType
from core.managed_document_storage import (
    EmptyManagedFileError,
    ManagedDocumentStorage,
    UnsafeStorageKeyError,
)
from core.schemas import AuditEntry, Bid

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
FIRST_BYTES = b"ContractIQ TASK-08 synthetic solicitation version one."
SECOND_BYTES = b"ContractIQ TASK-08 synthetic solicitation version two."


class SequenceIds:
    """Deterministic UUID factory for reproducible validation evidence."""

    def __init__(self, start: int = 1) -> None:
        self.value = start

    def __call__(self) -> UUID:
        value = UUID(int=self.value)
        self.value += 1
        return value


class FailingDocumentRepository(DocumentRepository):
    """Injected database/audit failure after service-side file placement."""

    def create_with_first_version(
        self,
        document: ControlledDocument,
        version: DocumentVersion,
        audit: AuditEntry,
    ) -> None:
        raise sqlite3.IntegrityError("induced validation audit/database failure")


def _bid() -> Bid:
    return Bid(
        bid_id="B-2026-0808",
        customer="Synthetic Customer",
        customer_type=CustomerType.EPC,
        project_name="TASK-08 Synthetic Bid",
        sales_owner="Synthetic Sales",
        bc_owner="Synthetic Coordinator",
        release_date=date(2026, 8, 1),
        customer_due_date=date(2026, 8, 31),
        internal_due_date=date(2026, 8, 28),
        estimated_value=Decimal("1000"),
        classification=BidLevel.LEVEL_1,
        created_at=NOW,
        updated_at=NOW,
    )


def _counts(service: DocumentService, bids: BidRepository) -> tuple[int, int, int, int]:
    return (
        len(service.repository.list_documents()),
        len(bids.list_audit()),
        len(list(service.storage.iter_managed_keys())),
        len(service.storage.staging_files()),
    )


def main() -> None:
    """Run the fixed synthetic acceptance and failure-cleanup scenario."""
    with TemporaryDirectory(prefix="contractiq-task08-") as raw_temp:
        temp_root = Path(raw_temp)
        db = Database(temp_root / "task08.db")
        bids = BidRepository(db)
        bid = _bid()
        bids.create_bid(bid)
        repository = DocumentRepository(db)
        storage = ManagedDocumentStorage(temp_root / "managed", max_bytes=1024 * 1024)
        service = DocumentService(
            repository,
            bids,
            storage,
            now_factory=lambda: NOW,
            id_factory=SequenceIds(),
        )

        document, first = service.register_document(
            {
                "bid_id": bid.bid_id,
                "title": "Synthetic RFP",
                "category": "SOLICITATION",
                "version_label": "Original",
                "issued_date": "2026-08-01",
            },
            io.BytesIO(FIRST_BYTES),
            "../../Synthetic RFP.txt",
            "text/plain",
            "validator",
        )
        assert first.original_filename == "Synthetic RFP.txt"
        assert first.byte_size == len(FIRST_BYTES)
        assert first.sha256_digest == hashlib.sha256(FIRST_BYTES).hexdigest()
        assert not Path(first.storage_key).is_absolute() and ".." not in first.storage_key
        assert first.version_state == DocumentVersionState.CURRENT
        assert len(bids.list_audit(bid.bid_id)) == 1

        updated, second = service.add_version(
            document.document_id,
            {
                "version_label": "Addendum 1 incorporated",
                "expected_document_version": document.version,
                "expected_current_version_id": first.document_version_id,
            },
            io.BytesIO(SECOND_BYTES),
            "Addendum 1.txt",
            "text/plain",
            "validator",
        )
        history = service.list_versions(document.document_id)
        assert second.predecessor_version_id == first.document_version_id
        assert history[0].document_version_id == second.document_version_id
        assert history[1].version_state == DocumentVersionState.SUPERSEDED
        assert sum(v.version_state == DocumentVersionState.CURRENT for v in history) == 1
        assert len(bids.list_audit(bid.bid_id)) == 2

        downloaded, stream = service.open_download(second.document_version_id)
        with stream:
            assert stream.read() == SECOND_BYTES
        assert downloaded == second
        assert service.verify_integrity(second.document_version_id).status == IntegrityStatus.OK

        managed_path = storage.resolve(second.storage_key)
        evidence_before = repository.get_version(second.document_version_id)
        audit_before = list(bids.list_audit(bid.bid_id))
        managed_path.write_bytes(b"X" * len(SECOND_BYTES))
        assert (
            service.verify_integrity(second.document_version_id).status
            == IntegrityStatus.HASH_MISMATCH
        )
        assert repository.get_version(second.document_version_id) == evidence_before
        assert bids.list_audit(bid.bid_id) == audit_before
        managed_path.write_bytes(SECOND_BYTES)
        assert service.verify_integrity(second.document_version_id).status == IntegrityStatus.OK

        before_duplicate = _counts(service, bids)
        try:
            service.add_version(
                document.document_id,
                {
                    "version_label": "Duplicate",
                    "expected_document_version": updated.version,
                    "expected_current_version_id": second.document_version_id,
                },
                io.BytesIO(SECOND_BYTES),
                "duplicate.txt",
                None,
                "validator",
            )
        except DuplicateDocumentVersionError:
            pass
        else:
            raise AssertionError("duplicate bytes were not rejected")
        assert _counts(service, bids) == before_duplicate

        for source, title, expected_error in (
            (io.BytesIO(b""), "Empty", EmptyManagedFileError),
            (io.BytesIO(b"not used"), "   ", ValidationError),
        ):
            before_failure = _counts(service, bids)
            try:
                service.register_document(
                    {
                        "bid_id": bid.bid_id,
                        "title": title,
                        "category": "SOLICITATION",
                        "version_label": "Original",
                    },
                    source,
                    "synthetic.txt",
                    None,
                    "validator",
                )
            except expected_error:
                pass
            else:
                raise AssertionError(f"{expected_error.__name__} was not raised")
            assert _counts(service, bids) == before_failure

        before_traversal = _counts(service, bids)
        try:
            storage.resolve("../../outside")
        except UnsafeStorageKeyError:
            pass
        else:
            raise AssertionError("traversal key was not rejected")
        assert _counts(service, bids) == before_traversal

        failing = DocumentService(
            FailingDocumentRepository(db),
            bids,
            storage,
            now_factory=lambda: NOW,
            id_factory=SequenceIds(100),
        )
        before_database_failure = _counts(service, bids)
        try:
            failing.register_document(
                {
                    "bid_id": bid.bid_id,
                    "title": "Rollback proof",
                    "category": "OTHER",
                    "version_label": "Original",
                },
                io.BytesIO(b"rollback-file-evidence"),
                "rollback.txt",
                "text/plain",
                "validator",
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("injected database failure was not raised")
        assert _counts(service, bids) == before_database_failure

        diagnostic = service.diagnose_storage()
        assert diagnostic.unreferenced_storage_keys == []
        assert all(
            result.status == IntegrityStatus.OK for result in diagnostic.committed_file_results
        )
        assert storage.staging_files() == []

        print("TASK-08 validation: PASS")
        print("Migration: task_08_document_control_v1 (clean database)")
        print("Synthetic versions: 2; exactly one CURRENT; predecessor lineage verified")
        print("SHA-256/download/integrity: exact bytes, OK -> HASH_MISMATCH -> OK")
        print("Failures: duplicate, empty, traversal, invalid title, database cleanup verified")
        print("Network/Alice/cloud: unused")


if __name__ == "__main__":
    main()
