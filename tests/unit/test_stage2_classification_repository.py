from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.bid_package_repository import BidPackageRepository
from core.bid_repository import BidRepository
from core.database import Database
from core.deliverable_discovery import (
    DeliverableProposal,
    DiscoveryCriticality,
    DiscoveryRun,
    ExtractionStatus,
    ExtractionUnit,
    LocatorType,
    ProposalReviewDecision,
    ProposalReviewEvent,
    RunStatus,
    SourceCitation,
    normalize_dedup_key,
)
from core.deliverable_discovery_classification import (
    CLASSIFICATIONS_TABLE,
    RECLASSIFICATIONS_TABLE,
    record_destination,
)
from core.deliverable_discovery_repository import DeliverableDiscoveryRepository
from core.deliverable_repository import DeliverableRepository
from core.enums import BidLevel, BidStatus, CustomerType, Gate
from core.requirement_lifecycle import (
    AppliesAt,
    ClassifiedBy,
    Confidence,
    DestinationKind,
    DiscoveryLifecycle,
    ItemKind,
    ProposalClassification,
    RequirementStatus,
    destination_for,
    guard_classification,
)
from core.schemas import Bid

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)
BID_ID = "B-2026-0200"


def _repository(tmp_path: Path) -> DeliverableDiscoveryRepository:
    database = Database(tmp_path / "stage2-a.db")
    BidRepository(database).create_bid(
        Bid(
            bid_id=BID_ID,
            customer="Synthetic Customer",
            customer_type=CustomerType.END_USER,
            project_name="Synthetic Unit A",
            sales_owner="Sales",
            bc_owner="Reviewer",
            release_date=date(2026, 10, 1),
            customer_due_date=date(2026, 10, 30),
            internal_due_date=date(2026, 10, 25),
            estimated_value=Decimal("1000"),
            classification=BidLevel.LEVEL_0,
            current_gate=Gate.G0,
            status=BidStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    BidPackageRepository(database)
    DeliverableRepository(database)
    return DeliverableDiscoveryRepository(database)


def _classification(
    lifecycle: DiscoveryLifecycle = DiscoveryLifecycle.BID_RETURNABLE,
) -> ProposalClassification:
    return ProposalClassification(
        lifecycle=lifecycle,
        model_lifecycle=lifecycle,
        requirement_status=RequirementStatus.REQUIRED,
        item_kind=ItemKind.SUBMISSION_DOCUMENT,
        description="Submit the signed form.",
        confidence=Confidence.HIGH,
        classified_by=ClassifiedBy.MODEL,
    )


def _create_proposal(
    repository: DeliverableDiscoveryRepository,
    *,
    classification: ProposalClassification | None,
    operation: str = "initial",
    locator: str = "p.1",
    source_id: str = "DV-SYNTHETIC-1",
) -> DeliverableProposal:
    run = DiscoveryRun(
        bid_id=BID_ID,
        operation_id=operation,
        status=RunStatus.SUCCEEDED,
        proposal_count=1,
        actor="Reviewer",
        started_at=NOW,
        completed_at=NOW,
    )
    text = "The Bidder shall submit the signed form with its tender."
    extraction = ExtractionUnit(
        bid_id=BID_ID,
        run_id=run.run_id,
        document_version_id=source_id,
        locator_type=LocatorType.PAGE,
        locator=locator,
        text_excerpt=text,
        char_count=len(text),
        status=ExtractionStatus.OK,
        created_at=NOW,
    )
    proposal = DeliverableProposal(
        bid_id=BID_ID,
        run_id=run.run_id,
        title="Signed Form",
        category="Form",
        criticality=DiscoveryCriticality.REQUIRED,
        classification=classification,
        sources=(
            SourceCitation(
                document_version_id=source_id,
                locator_type=LocatorType.PAGE,
                locator=locator,
                excerpt=text,
                quote=text,
            ),
        ),
        dedup_key=normalize_dedup_key(
            BID_ID,
            "Signed Form",
            "Form",
            classification if classification is not None else None,
        ),
        created_at=NOW,
    )
    repository.create_run(run, [extraction], [proposal], "Reviewer")
    return proposal


def test_vocabulary_guards_and_deterministic_routing() -> None:
    passage = "The Bidder shall submit the signed form with its tender."
    guarded = guard_classification(
        {
            "lifecycle": "BID_RETURNABLE",
            "requirement_status": "REQUIRED",
            "item_kind": "SUBMISSION_DOCUMENT",
            "description": "Submit the signed form.",
            "applies_at": None,
            "responsible_party": "Bidder",
            "dependencies": None,
            "confidence": "HIGH",
            "review_reason": None,
            "source_section": "Instructions",
            "source_quote": passage,
            "timing_text": "with its tender",
            "condition_text": None,
            "category": "Form",
        },
        passage,
    )
    assert guarded.lifecycle is DiscoveryLifecycle.BID_RETURNABLE
    assert guarded.responsible_party == "Bidder"
    assert destination_for(guarded) is DestinationKind.DELIVERABLE

    downgraded = guard_classification(
        {
            **guarded.model_dump(mode="json"),
            "source_quote": "This sentence does not occur anywhere in the source.",
            "timing_text": None,
            "condition_text": None,
            "category": "Form",
        },
        passage,
    )
    assert downgraded.lifecycle is DiscoveryLifecycle.NEEDS_REVIEW
    assert downgraded.model_lifecycle is DiscoveryLifecycle.BID_RETURNABLE
    assert downgraded.review_reason == "The supporting sentence was not found in the source text"

    with pytest.raises(ValidationError, match="applies_at is only valid"):
        ProposalClassification(
            **{
                **_classification().model_dump(),
                "applies_at": AppliesAt.WITH_BID,
            }
        )


def test_migration_is_idempotent_and_legacy_rows_keep_stage1_semantics(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    legacy = _create_proposal(repository, classification=None)

    DeliverableDiscoveryRepository(repository.db)
    DeliverableDiscoveryRepository(repository.db)

    row = repository.get_proposal(legacy.proposal_id)
    assert row is not None
    assert row["classification"] is None
    assert row["lifecycle"] == "NEEDS_REVIEW"
    assert row["legacy"] is True
    assert row["review_reason"] == "Not classified (recorded before lifecycle classification)"


def test_effective_overlay_and_insert_only_triggers(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    proposal = _create_proposal(repository, classification=_classification())

    original = repository.get_proposal(proposal.proposal_id)
    assert original is not None
    assert original["lifecycle"] == "BID_RETURNABLE"
    assert original["lifecycle_source"] == "model"

    assert repository.reclassify(
        BID_ID,
        [proposal.proposal_id],
        DiscoveryLifecycle.POST_AWARD,
        "The clause applies after award.",
        "Reviewer",
        item_kind=ItemKind.COMMERCIAL_QUALIFICATION,
        commercial_topic="WARRANTY",
    ) == [{"proposal_id": proposal.proposal_id}]
    assert repository.reclassify(
        BID_ID,
        [proposal.proposal_id],
        DiscoveryLifecycle.CONDITIONAL,
        "The customer may request it with the bid.",
        "Reviewer",
        applies_at=AppliesAt.WITH_BID,
    ) == [{"proposal_id": proposal.proposal_id}]

    effective = repository.get_proposal(proposal.proposal_id)
    assert effective is not None
    assert effective["lifecycle"] == "CONDITIONAL"
    assert effective["applies_at"] == "WITH_BID"
    assert effective["item_kind"] == "COMMERCIAL_QUALIFICATION"
    assert effective["commercial_topic"] == "WARRANTY"
    assert effective["lifecycle_source"] == "reviewer"
    assert len(effective["reclassifications"]) == 2
    assert (
        effective["reclassifications"][1]["supersedes_event_id"]
        == effective["reclassifications"][0]["event_id"]
    )

    with repository._conn() as conn:  # type: ignore[attr-defined]
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                f"UPDATE {CLASSIFICATIONS_TABLE} SET description='changed' WHERE proposal_id=?",
                (proposal.proposal_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute(
                f"DELETE FROM {RECLASSIFICATIONS_TABLE} WHERE proposal_id=?",
                (proposal.proposal_id,),
            )


def test_reclassify_refusals_and_audit_failure_are_atomic(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    proposal = _create_proposal(repository, classification=_classification())

    with pytest.raises(ValueError, match="Explain why"):
        repository.reclassify(
            BID_ID, [proposal.proposal_id], DiscoveryLifecycle.POST_AWARD, " ", "Reviewer"
        )
    with pytest.raises(ValueError, match="only valid"):
        repository.reclassify(
            BID_ID,
            [proposal.proposal_id],
            DiscoveryLifecycle.POST_AWARD,
            "Reason",
            "Reviewer",
            applies_at=AppliesAt.POST_AWARD,
        )
    unknown = repository.reclassify(
        BID_ID, ["DDP-UNKNOWN"], DiscoveryLifecycle.POST_AWARD, "Reason", "Reviewer"
    )
    assert "not found" in unknown[0]["error"]

    with repository._conn() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """CREATE TRIGGER fail_reclassification_audit BEFORE INSERT ON audit_log
            WHEN NEW.action='deliverable_discovery_proposal_reclassified'
            BEGIN SELECT RAISE(ABORT,'injected audit failure'); END"""
        )
    failed = repository.reclassify(
        BID_ID,
        [proposal.proposal_id],
        DiscoveryLifecycle.POST_AWARD,
        "Reason",
        "Reviewer",
    )
    assert "injected audit failure" in failed[0]["error"]
    assert repository.get_proposal(proposal.proposal_id)["reclassifications"] == []  # type: ignore[index]

    with repository._conn() as conn:  # type: ignore[attr-defined]
        conn.execute("DROP TRIGGER fail_reclassification_audit")
    accepted_event = ProposalReviewEvent(
        proposal_id=proposal.proposal_id,
        bid_id=BID_ID,
        decision=ProposalReviewDecision.ACCEPTED,
        reviewer="Reviewer",
        linked_deliverable_id="DEL-SYNTHETIC",
        reviewed_at=NOW,
    )
    repository.add_review_event(accepted_event, "Reviewer")
    accepted = repository.reclassify(
        BID_ID,
        [proposal.proposal_id],
        DiscoveryLifecycle.POST_AWARD,
        "Reason",
        "Reviewer",
    )
    assert "accepted proposals" in accepted[0]["error"]
    with repository._conn() as conn:  # type: ignore[attr-defined]
        destination = record_destination(
            conn,
            proposal_id=proposal.proposal_id,
            event_id=accepted_event.event_id,
            bid_id=BID_ID,
            destination_kind=DestinationKind.DELIVERABLE,
            record_id="DEL-SYNTHETIC",
            reused=False,
        )
    assert repository.get_proposal(proposal.proposal_id)["destination"] == destination  # type: ignore[index]


def test_dedup_index_and_append_citations_are_idempotent(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    classification = _classification()
    proposal = _create_proposal(repository, classification=classification)
    legacy_a = normalize_dedup_key(BID_ID, "Signed Form", "Form")
    legacy_b = normalize_dedup_key(BID_ID, "Signed Form", "Form")
    assert legacy_a == legacy_b
    assert normalize_dedup_key(BID_ID, "Signed Form", "A", classification) == normalize_dedup_key(
        BID_ID, "Signed Form", "B", classification
    )
    assert repository.existing_dedup_index(BID_ID) == {proposal.dedup_key: proposal.proposal_id}

    run = DiscoveryRun(
        bid_id=BID_ID,
        operation_id="second-source",
        status=RunStatus.SUCCEEDED,
        proposal_count=0,
        actor="Reviewer",
        started_at=NOW,
        completed_at=NOW,
    )
    text = "The Bidder shall submit the signed form with its tender."
    unit = ExtractionUnit(
        bid_id=BID_ID,
        run_id=run.run_id,
        document_version_id="DV-SYNTHETIC-2",
        locator_type=LocatorType.PAGE,
        locator="p.8",
        text_excerpt=text,
        char_count=len(text),
        status=ExtractionStatus.OK,
        created_at=NOW,
    )
    repository.create_run(run, [unit], [], "Reviewer")
    citation = SourceCitation(
        document_version_id="DV-SYNTHETIC-2",
        locator_type=LocatorType.PAGE,
        locator="p.8",
        excerpt=text,
        quote=text,
    )
    repository.append_citations(proposal.proposal_id, [citation])
    repository.append_citations(proposal.proposal_id, [citation])

    stored = repository.get_proposal(proposal.proposal_id)
    assert stored is not None
    assert len(stored["sources"]) == 2
    with repository._conn() as conn:  # type: ignore[attr-defined]
        audits = conn.execute(
            "SELECT count(*) FROM audit_log WHERE action=?",
            ("deliverable_discovery_citation_appended",),
        ).fetchone()[0]
    assert audits == 1
