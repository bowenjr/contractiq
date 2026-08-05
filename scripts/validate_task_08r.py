"""Deterministic TASK-08R validation with isolated synthetic local evidence."""

import io
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from core.bid_repository import BidRepository
from core.database import Database
from core.document_control import DocumentLifecycle, DocumentVersionState, IntegrityStatus
from core.document_repository import DocumentRepository
from core.document_service import DocumentService
from core.enums import BidLevel, CustomerType
from core.managed_document_storage import ManagedDocumentStorage
from core.schemas import Bid

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
FIRST = b"TASK-08R isolated synthetic evidence version one"
SECOND = b"TASK-08R isolated synthetic evidence version two"


class SequenceIds:
    """Return deterministic UUIDs for reproducible evidence identifiers."""

    def __init__(self) -> None:
        self._next = 1

    def __call__(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


def _bid() -> Bid:
    return Bid(
        bid_id="B-2026-0809",
        customer="Synthetic Customer",
        customer_type=CustomerType.EPC,
        project_name="TASK-08R Isolated Validation",
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


def _must_reject(conn: sqlite3.Connection, sql: str, value: str) -> None:
    try:
        conn.execute(sql, (value,))
    except sqlite3.IntegrityError:
        conn.rollback()
    else:
        raise AssertionError(f"database mutation was not rejected: {sql}")


def main() -> None:
    """Run all bounded TASK-08R acceptance checks without network access."""
    with TemporaryDirectory(prefix="contractiq-task08r-") as raw_temp:
        root = Path(raw_temp)
        database = Database(root / "task08r.db")

        # The document repository must own its migration prerequisites.
        repository = DocumentRepository(database)
        assert DocumentRepository(database).list_documents() == []
        bids = BidRepository(database)
        bid = _bid()
        bids.create_bid(bid)
        storage = ManagedDocumentStorage(root / "managed", 1024 * 1024)
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
                "title": "Synthetic controlled document",
                "category": "SOLICITATION",
                "version_label": "Original",
            },
            io.BytesIO(FIRST),
            "synthetic.txt",
            "text/plain",
            "validator",
        )
        updated, second = service.add_version(
            document.document_id,
            {
                "version_label": "Revision 1",
                "expected_document_version": document.version,
                "expected_current_version_id": first.document_version_id,
            },
            io.BytesIO(SECOND),
            "synthetic-revision.txt",
            "text/plain",
            "validator",
        )
        history = service.list_versions(document.document_id)
        assert [item.version_state for item in history] == [
            DocumentVersionState.CURRENT,
            DocumentVersionState.SUPERSEDED,
        ]
        assert second.predecessor_version_id == first.document_version_id
        assert service.get_document(document.document_id).current_version_id == (
            second.document_version_id
        )

        with database._conn() as conn:
            _must_reject(
                conn,
                "UPDATE documents SET bid_id = NULL WHERE id = ?",
                document.document_id,
            )
            _must_reject(
                conn,
                "UPDATE document_versions SET version_label = 'changed' "
                "WHERE document_version_id = ?",
                second.document_version_id,
            )
            _must_reject(
                conn,
                "DELETE FROM document_versions WHERE document_version_id = ?",
                first.document_version_id,
            )

        evidence, stream = service.open_download(second.document_version_id)
        with stream:
            assert stream.read() == SECOND
        assert evidence == second
        assert service.verify_integrity(second.document_version_id).status == IntegrityStatus.OK

        withdrawn = service.withdraw(document.document_id, updated.version, "validator")
        try:
            service.add_version(
                document.document_id,
                {
                    "version_label": "Forbidden",
                    "expected_document_version": withdrawn.version,
                    "expected_current_version_id": second.document_version_id,
                },
                io.BytesIO(b"forbidden successor"),
                "forbidden.txt",
                "text/plain",
                "validator",
            )
        except ValueError as exc:
            assert "withdrawn" in str(exc)
        else:
            raise AssertionError("withdrawn successor was not rejected")
        corrected = service.update_metadata(
            document.document_id,
            {"expected_version": withdrawn.version, "title": "Corrected evidence title"},
            "validator",
        )
        assert corrected.lifecycle_state == DocumentLifecycle.WITHDRAWN
        assert corrected.bid_id == document.bid_id
        assert corrected.current_version_id == second.document_version_id

        with database._conn() as conn:
            conn.execute("DROP TRIGGER validate_controlled_document_update")
            conn.execute(
                "UPDATE documents SET current_version_id = 'DV-synthetic-missing' WHERE id = ?",
                (document.document_id,),
            )
        repository = DocumentRepository(database)
        before_audit = list(bids.list_audit(bid.bid_id))
        issues = repository.diagnose_logical_integrity()
        assert any(issue.document_id == document.document_id for issue in issues)
        assert repository.list_register_entries()[0].logical_issues
        assert bids.list_audit(bid.bid_id) == before_audit
        assert storage.staging_files() == []
        assert len(list(storage.iter_managed_keys())) == 2

        print("TASK-08R validation: PASS")
        print("Migration: isolated direct construction and idempotent re-run verified")
        print("Identity/version SQLite invariants: direct mutation and deletion rejected")
        print("Successor: exactly one CURRENT with predecessor and pointer agreement")
        print("Withdrawal: successor rejected; audited descriptive correction allowed")
        print("Diagnostics: synthetic pointer corruption reported without repair")
        print("File evidence: exact-byte download and SHA-256 integrity OK")
        print("Network/Alice/cloud/production data: unused")


if __name__ == "__main__":
    main()
