"""Deterministic TASK-09 validation using isolated synthetic local evidence."""

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
from core.document_repository import DocumentRepository
from core.document_service import DocumentService
from core.enums import BidLevel, CustomerType
from core.managed_document_storage import ManagedDocumentStorage
from core.requirement_repository import REQUIREMENT_MIGRATION_ID, RequirementRepository
from core.requirement_service import RequirementService
from core.schemas import Bid
from core.work_item_repository import WorkItemRepository
from core.work_item_service import MyDayService

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
AS_OF = date(2026, 8, 5)
FIRST = b"TASK-09 synthetic solicitation original"
SECOND = b"TASK-09 synthetic addendum incorporated"


class SequenceIds:
    """Return deterministic UUIDs for reproducible synthetic evidence."""

    def __init__(self, start: int) -> None:
        self._next = start

    def __call__(self) -> UUID:
        result = UUID(int=self._next)
        self._next += 1
        return result


def _bid(identifier: str, name: str) -> Bid:
    return Bid(
        bid_id=identifier,
        customer="Synthetic Customer",
        customer_type=CustomerType.EPC,
        project_name=name,
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


def _register(service: DocumentService, bid: Bid, label: str) -> tuple[str, str]:
    document, version = service.register_document(
        {
            "bid_id": bid.bid_id,
            "title": f"{label} controlled solicitation",
            "category": "SOLICITATION",
            "version_label": "Original",
        },
        io.BytesIO(FIRST + label.encode()),
        f"{label}.txt",
        "text/plain",
        "validator",
    )
    return document.document_id, version.document_version_id


def _table_rows(db: Database, table: str) -> list[tuple[object, ...]]:
    with db._conn() as conn:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    return [tuple(row) for row in rows]


def main() -> None:
    """Run the bounded TASK-09 acceptance proof without network or production data."""
    with TemporaryDirectory(prefix="contractiq-task09-") as raw_temp:
        root = Path(raw_temp)
        database = Database(root / "task09.db")
        bids = BidRepository(database)
        documents = DocumentRepository(database)
        requirements = RequirementRepository(database)
        assert RequirementRepository(database).list() == []
        assert REQUIREMENT_MIGRATION_ID == "task_09_requirements_v1"

        bid_a = _bid("B-2026-0901", "Synthetic Alpha")
        bid_b = _bid("B-2026-0902", "Synthetic Beta")
        bids.create_bid(bid_a)
        bids.create_bid(bid_b)
        storage = ManagedDocumentStorage(root / "managed", 1024 * 1024)
        document_service = DocumentService(
            documents,
            bids,
            storage,
            now_factory=lambda: NOW,
            id_factory=SequenceIds(1),
        )
        document_a_id, version_a_id = _register(document_service, bid_a, "alpha")
        _, version_b_id = _register(document_service, bid_b, "beta")
        service = RequirementService(
            requirements,
            bids,
            documents,
            now_factory=lambda: NOW,
            id_factory=SequenceIds(100),
        )

        explicit = service.create_requirement(
            {
                "bid_id": bid_a.bid_id,
                "title": "Mandatory compliance schedule",
                "statement": "Submit the completed synthetic compliance schedule.",
                "origin": "EXPLICIT",
                "category": "SUBMISSION",
                "significance": "MANDATORY",
                "source_document_version_id": version_a_id,
                "source_clause": "4.2",
            },
            "validator",
        )
        internal = service.create_requirement(
            {
                "bid_id": bid_a.bid_id,
                "title": "Internal scored review",
                "statement": "Complete the synthetic internal review.",
                "origin": "INTERNAL",
                "category": "COMMERCIAL",
                "significance": "SCORED",
                "due_date": AS_OF,
            },
            "validator",
        )
        assert service.coverage(bid_id=bid_a.bid_id, as_of_date=AS_OF).high_attention == 1

        database.create_document({"id": "LEGACY-09", "filename": "legacy.txt"})
        bids.attach_document_to_bid("LEGACY-09", bid_a.bid_id)
        requirement_count = len(requirements.list())
        audit_count = len(bids.list_audit(bid_a.bid_id))
        for invalid_source in (version_b_id, "LEGACY-09"):
            try:
                service.create_requirement(
                    {
                        "bid_id": bid_a.bid_id,
                        "title": "Rejected source",
                        "statement": "This record must not persist.",
                        "origin": "EXPLICIT",
                        "category": "OTHER",
                        "significance": "MANDATORY",
                        "source_document_version_id": invalid_source,
                        "source_clause": "1",
                    },
                    "validator",
                )
            except ValueError:
                pass
            else:
                raise AssertionError("cross-bid or legacy source was accepted")
        assert len(requirements.list()) == requirement_count
        assert len(bids.list_audit(bid_a.bid_id)) == audit_count

        controlled_a = document_service.get_document(document_a_id)
        _, second = document_service.add_version(
            document_a_id,
            {
                "version_label": "Addendum 1 incorporated",
                "expected_document_version": controlled_a.version,
                "expected_current_version_id": version_a_id,
            },
            io.BytesIO(SECOND),
            "addendum.txt",
            "text/plain",
            "validator",
        )
        assert service.get_requirement(explicit.requirement_id).source_document_version_id == (
            version_a_id
        )
        assert second.document_version_id != version_a_id

        assigned = service.update_metadata(
            explicit.requirement_id,
            {"expected_version": explicit.version, "owner": "owner", "due_date": AS_OF},
            "validator",
        )
        completed = service.update_workflow(
            explicit.requirement_id,
            {
                "expected_version": assigned.version,
                "disposition": "COMPLY",
                "response_text": "Included in the synthetic response.",
                "work_state": "COMPLETE",
            },
            "validator",
        )
        accepted = service.record_review(
            explicit.requirement_id,
            {
                "expected_version": completed.version,
                "review_state": "ACCEPTED",
                "reviewer": "independent reviewer",
            },
            "validator",
        )
        assert accepted.fully_closed

        before_requirement = service.get_requirement(explicit.requirement_id)
        before_audit = list(bids.list_audit(bid_a.bid_id))
        invalid_updates = (
            {
                "expected_version": accepted.version,
                "disposition": "NOT_APPLICABLE",
                "work_state": "OPEN",
            },
            {
                "expected_version": accepted.version,
                "disposition": "UNASSESSED",
                "work_state": "COMPLETE",
            },
        )
        for invalid in invalid_updates:
            try:
                service.update_workflow(explicit.requirement_id, invalid, "validator")
            except ValidationError:
                pass
            else:
                raise AssertionError("invalid workflow was accepted")
        try:
            service.update_metadata(
                explicit.requirement_id,
                {"expected_version": 1, "owner": "stale"},
                "validator",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("stale update was accepted")
        assert service.get_requirement(explicit.requirement_id) == before_requirement
        assert bids.list_audit(bid_a.bid_id) == before_audit

        collision_id = f"AUD-{UUID(int=999)}"
        with database._conn() as conn:
            conn.execute(
                "INSERT INTO audit_log VALUES (?,?,?,?,?,?)",
                (collision_id, bid_a.bid_id, "validator", "seed", "{}", NOW.isoformat()),
            )
        collision_service = RequirementService(
            requirements,
            bids,
            documents,
            now_factory=lambda: NOW,
            id_factory=lambda: UUID(int=999),
        )
        before_collision = collision_service.get_requirement(internal.requirement_id)
        try:
            collision_service.update_metadata(
                internal.requirement_id,
                {"expected_version": internal.version, "owner": "must roll back"},
                "validator",
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("induced audit failure did not fail")
        assert collision_service.get_requirement(internal.requirement_id) == before_collision

        document_rows_before = _table_rows(database, "documents")
        version_rows_before = _table_rows(database, "document_versions")
        byte_hashes_before = {
            key: hashlib.sha256(storage.resolve(key).read_bytes()).hexdigest()
            for key in storage.iter_managed_keys()
        }
        projection = MyDayService(
            WorkItemRepository(database),
            bids,
            database,
            requirement_repository=requirements,
        ).get_my_day(as_of=AS_OF)
        assert [item.requirement.requirement_id for item in projection.requirement_attention] == [
            internal.requirement_id
        ]
        assert projection.counts.requirement_due_today == 1
        coverage = service.coverage(bid_id=bid_a.bid_id, as_of_date=AS_OF)
        assert coverage.total_active == 2
        assert coverage.assessed.numerator == coverage.fully_closed.numerator == 1
        assert coverage.due_today == 1

        withdrawn = service.withdraw(
            internal.requirement_id,
            {"expected_version": internal.version},
            "validator",
        )
        assert service.get_requirement(internal.requirement_id) == withdrawn
        assert service.coverage(bid_id=bid_a.bid_id, as_of_date=AS_OF).total_active == 1
        assert service.audit_history(internal.requirement_id)[0].action == "requirement_withdrawn"
        assert _table_rows(database, "documents") == document_rows_before
        assert _table_rows(database, "document_versions") == version_rows_before
        assert {
            key: hashlib.sha256(storage.resolve(key).read_bytes()).hexdigest()
            for key in storage.iter_managed_keys()
        } == byte_hashes_before
        assert service.source_choices(bid_a.bid_id).available
        assert bids.get_bid(bid_a.bid_id) == bid_a
        assert service.coverage(bid_id=None, as_of_date=AS_OF).total_active == 1

        print("TASK-09 validation: PASS")
        print(f"Migration: {REQUIREMENT_MIGRATION_ID}; clean and idempotent")
        print("Sources: exact immutable version retained; cross-bid/legacy rejected")
        print("Workflow: metadata, response, COMPLETE, ACCEPTED, withdrawal audited")
        print("Atomicity: invalid, stale, and induced audit failures left no mutation")
        print("Coverage/My Day/readiness adjacency: deterministic at 2026-08-05")
        print("Documents: rows and synthetic managed byte hashes unchanged by requirements")
        print("Network/Alice/cloud/production data: unused")


if __name__ == "__main__":
    main()
