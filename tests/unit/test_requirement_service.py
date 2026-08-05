import io
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from core.bid_repository import BidRepository
from core.database import Database
from core.document_repository import DocumentRepository
from core.document_service import DocumentService
from core.managed_document_storage import ManagedDocumentStorage
from core.requirement_repository import (
    RequirementRepository,
    RequirementSourceError,
    StaleRequirementError,
)
from core.requirement_service import RequirementService
from core.requirements import RequirementLifecycle, RequirementReviewState, RequirementWorkState
from core.schemas import Bid

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)


class Ids:
    def __init__(self, start: int = 100) -> None:
        self.value = start

    def __call__(self) -> UUID:
        result = UUID(int=self.value)
        self.value += 1
        return result


def _services(
    tmp_path: Path,
    db: Database,
    bids: BidRepository,
) -> tuple[DocumentService, RequirementService]:
    documents = DocumentRepository(db)
    document_service = DocumentService(
        documents,
        bids,
        ManagedDocumentStorage(tmp_path / "managed", 1024 * 1024),
        now_factory=lambda: NOW,
        id_factory=Ids(1),
    )
    requirements = RequirementService(
        RequirementRepository(db),
        bids,
        documents,
        now_factory=lambda: NOW,
        id_factory=Ids(100),
    )
    return document_service, requirements


def _document(service: DocumentService, bid: Bid):
    return service.register_document(
        {
            "bid_id": bid.bid_id,
            "title": "Synthetic Solicitation",
            "category": "SOLICITATION",
            "version_label": "Original",
        },
        io.BytesIO(b"synthetic source bytes"),
        "source.txt",
        "text/plain",
        "author",
    )


def _explicit(bid: Bid, version_id: str) -> dict[str, object]:
    return {
        "bid_id": bid.bid_id,
        "title": "Submit compliance schedule",
        "statement": "The bidder shall submit a completed compliance schedule.",
        "origin": "EXPLICIT",
        "category": "SUBMISSION",
        "significance": "MANDATORY",
        "source_document_version_id": version_id,
        "source_clause": "4.2",
    }


def _internal(bid: Bid) -> dict[str, object]:
    return {
        "bid_id": bid.bid_id,
        "title": "Internal review target",
        "statement": "Complete the internal commercial review.",
        "origin": "INTERNAL",
        "category": "COMMERCIAL",
        "significance": "SCORED",
    }


def test_explicit_source_is_exact_and_does_not_follow_new_current_version(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    documents, requirements = _services(tmp_path, tmp_db, bid_repo)
    document, first = _document(documents, valid_bid)
    requirement = requirements.create_requirement(
        _explicit(valid_bid, first.document_version_id), "author"
    )
    updated, second = documents.add_version(
        document.document_id,
        {
            "version_label": "Addendum 1 incorporated",
            "expected_document_version": document.version,
            "expected_current_version_id": first.document_version_id,
        },
        io.BytesIO(b"synthetic addendum bytes"),
        "addendum.txt",
        "text/plain",
        "author",
    )
    loaded = requirements.get_requirement(requirement.requirement_id)
    assert updated.current_version_id == second.document_version_id
    assert loaded.source_document_version_id == first.document_version_id
    assert requirements.detail(loaded.requirement_id).source.version_state == "SUPERSEDED"


def test_internal_without_source_succeeds_and_invalid_input_mutates_nothing(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    _, service = _services(tmp_path, tmp_db, bid_repo)
    created = service.create_requirement(_internal(valid_bid), "author")
    assert created.source_document_version_id is None
    before_audit = bid_repo.list_audit(valid_bid.bid_id)
    with pytest.raises(ValidationError):
        service.create_requirement({**_internal(valid_bid), "title": " "}, "author")
    with pytest.raises(ValidationError, match="interpretation must be distinct"):
        service.create_requirement(
            {
                **_internal(valid_bid),
                "interpretation": "Complete the internal commercial review.",
            },
            "author",
        )
    with pytest.raises(ValidationError, match="source locator"):
        service.create_requirement(
            {
                **_internal(valid_bid),
                "origin": "EXPLICIT",
                "source_document_version_id": "DV-00000000-0000-0000-0000-000000000001",
            },
            "author",
        )
    assert service.repository.list() == [created]
    assert bid_repo.list_audit(valid_bid.bid_id) == before_audit


def test_cross_bid_legacy_and_degraded_sources_are_rejected_without_mutation(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    other = valid_bid.model_copy(
        update={"bid_id": "B-2026-0002", "project_name": "Other synthetic bid"}
    )
    bid_repo.create_bid(valid_bid)
    bid_repo.create_bid(other)
    documents, service = _services(tmp_path, tmp_db, bid_repo)
    controlled, version = _document(documents, other)
    before = list(bid_repo.list_audit(valid_bid.bid_id))
    with pytest.raises(RequirementSourceError, match="does not belong"):
        service.create_requirement(_explicit(valid_bid, version.document_version_id), "author")
    tmp_db.create_document({"id": "LEGACY", "filename": "legacy.txt"})
    with pytest.raises(RequirementSourceError, match="not found"):
        service.create_requirement(_explicit(valid_bid, "LEGACY"), "author")
    with tmp_db._conn() as conn:
        conn.execute("DROP TRIGGER validate_controlled_document_update")
        conn.execute(
            "UPDATE documents SET current_version_id = 'DV-missing' WHERE id = ?",
            (controlled.document_id,),
        )
    with pytest.raises(RequirementSourceError, match="integrity review"):
        service.create_requirement(_explicit(other, version.document_version_id), "author")
    assert service.repository.list() == []
    assert bid_repo.list_audit(valid_bid.bid_id) == before


def test_workflow_review_reset_changes_required_and_full_closure(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    _, service = _services(tmp_path, tmp_db, bid_repo)
    item = service.create_requirement(_internal(valid_bid), "author")
    with pytest.raises(ValidationError, match="COMPLETE"):
        service.update_workflow(
            item.requirement_id,
            {"expected_version": 1, "disposition": "UNASSESSED", "work_state": "COMPLETE"},
            "author",
        )
    ready = service.update_workflow(
        item.requirement_id,
        {
            "expected_version": 1,
            "disposition": "COMPLY",
            "response_text": "Included in the synthetic response.",
            "work_state": "READY_FOR_REVIEW",
        },
        "author",
    )
    changes = service.record_review(
        item.requirement_id,
        {
            "expected_version": ready.version,
            "review_state": "CHANGES_REQUIRED",
            "reviewer": "independent reviewer",
        },
        "reviewer",
    )
    assert changes.work_state == RequirementWorkState.IN_PROGRESS
    final_work = service.update_workflow(
        item.requirement_id,
        {
            "expected_version": changes.version,
            "disposition": "COMPLY",
            "response_text": "Included in the corrected synthetic response.",
            "work_state": "COMPLETE",
        },
        "author",
    )
    accepted = service.record_review(
        item.requirement_id,
        {
            "expected_version": final_work.version,
            "review_state": "ACCEPTED",
            "reviewer": "independent reviewer",
        },
        "reviewer",
    )
    assert accepted.review_state == RequirementReviewState.ACCEPTED
    assert accepted.fully_closed
    revised = service.update_metadata(
        item.requirement_id,
        {
            "expected_version": accepted.version,
            "statement": "Complete the revised internal commercial review.",
        },
        "author",
    )
    assert revised.review_state == RequirementReviewState.NOT_REVIEWED
    assert revised.reviewer is None
    assert not revised.fully_closed


def test_stale_and_audit_failure_roll_back_authoritative_mutation(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bid_repo.create_bid(valid_bid)
    _, service = _services(tmp_path, tmp_db, bid_repo)
    item = service.create_requirement(_internal(valid_bid), "author")
    before_audit = list(bid_repo.list_audit(valid_bid.bid_id))
    with pytest.raises(StaleRequirementError):
        service.update_metadata(item.requirement_id, {"expected_version": 99, "owner": "x"}, "x")
    assert service.get_requirement(item.requirement_id) == item
    assert bid_repo.list_audit(valid_bid.bid_id) == before_audit

    def fail(_conn: sqlite3.Connection, _audit: object) -> None:
        raise sqlite3.IntegrityError("induced audit failure")

    monkeypatch.setattr(service.repository, "_insert_audit", fail)
    with pytest.raises(sqlite3.IntegrityError, match="induced"):
        service.update_metadata(
            item.requirement_id,
            {"expected_version": item.version, "owner": "new owner"},
            "author",
        )
    assert service.get_requirement(item.requirement_id) == item


def test_withdrawal_is_irreversible_and_preserves_history(
    tmp_path: Path,
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    _, service = _services(tmp_path, tmp_db, bid_repo)
    item = service.create_requirement(_internal(valid_bid), "author")
    withdrawn = service.withdraw(item.requirement_id, {"expected_version": 1}, "author")
    assert withdrawn.lifecycle_state == RequirementLifecycle.WITHDRAWN
    assert service.get_requirement(item.requirement_id) == withdrawn
    assert [event.action for event in service.audit_history(item.requirement_id)] == [
        "requirement_withdrawn",
        "requirement_created",
    ]
    with pytest.raises(ValueError, match="closed"):
        service.update_metadata(
            item.requirement_id,
            {"expected_version": withdrawn.version, "owner": "someone"},
            "author",
        )
    assert not hasattr(service, "delete")
    assert not hasattr(service.repository, "delete")
