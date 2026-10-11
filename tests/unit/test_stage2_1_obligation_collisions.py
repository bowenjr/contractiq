from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

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
)
from core.deliverable_discovery_collisions import (
    COLLISION_KEY_MARKER,
    DESCRIPTION_TOKEN_OVERLAP_THRESHOLD,
    collisions_between,
    description_token_overlap,
)
from core.deliverable_discovery_extraction import RawUnit
from core.deliverable_discovery_llm import ProposedCandidate, analyse_units
from core.deliverable_discovery_migrations import COLLISIONS_TABLE
from core.deliverable_discovery_repository import DeliverableDiscoveryRepository
from core.deliverable_discovery_service import _consolidate, split_known_duplicates
from core.deliverable_repository import DeliverableRepository
from core.enums import BidLevel, BidStatus, CustomerType, Gate
from core.requirement_lifecycle import (
    ClassifiedBy,
    Confidence,
    DiscoveryLifecycle,
    ItemKind,
    ProposalClassification,
    RequirementStatus,
)
from core.schemas import Bid
from core.vddr_table import interpret_vddr_unit

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)
BID_ID = "B-2026-0210"


class _ForbiddenModel:
    def complete_json(self, *_: object, **__: object) -> dict[str, object]:
        raise AssertionError("VDDR collision rules must not call an LLM")


def _repository(tmp_path: Path) -> tuple[Database, DeliverableDiscoveryRepository]:
    database = Database(tmp_path / "collision.db")
    BidRepository(database).create_bid(
        Bid(
            bid_id=BID_ID,
            customer="Synthetic Customer",
            customer_type=CustomerType.END_USER,
            project_name="Collision integrity",
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
    return database, DeliverableDiscoveryRepository(database)


def _classification(
    description: str,
    *,
    lifecycle: DiscoveryLifecycle = DiscoveryLifecycle.POST_AWARD,
    responsible_party: str | None = "Supplier",
    source_section: str | None = "Technical requirements",
) -> ProposalClassification:
    return ProposalClassification(
        lifecycle=lifecycle,
        model_lifecycle=lifecycle,
        requirement_status=RequirementStatus.REQUIRED,
        item_kind=ItemKind.SUBMISSION_DOCUMENT,
        description=description,
        responsible_party=responsible_party,
        confidence=Confidence.HIGH,
        source_section=source_section,
        classified_by=ClassifiedBy.MODEL,
    )


def _candidate(
    extraction_id: str,
    quote: str,
    description: str,
    *,
    title: str = "Monthly Progress Report",
    timing: str | None = None,
    condition: str | None = None,
    source_section: str | None = "Technical requirements",
) -> ProposedCandidate:
    classification = _classification(description, source_section=source_section)
    return ProposedCandidate(
        extraction_id=extraction_id,
        title=title,
        category="Reporting",
        criticality=(
            DiscoveryCriticality.CONDITIONAL
            if condition is not None
            else DiscoveryCriticality.REQUIRED
        ),
        condition_text=condition,
        due_date=None,
        requested_format=None,
        requested_recipient=None,
        proposed_owner=None,
        due_date_text=timing,
        source_quote=quote,
        description=description,
        classification=classification,
    )


def _unit(
    extraction_id: str,
    run_id: str,
    text: str,
    *,
    source_id: str,
    locator: str,
) -> ExtractionUnit:
    return ExtractionUnit(
        extraction_id=extraction_id,
        bid_id=BID_ID,
        run_id=run_id,
        document_version_id=source_id,
        locator_type=LocatorType.PAGE,
        locator=locator,
        text_excerpt=text,
        char_count=len(text),
        status=ExtractionStatus.OK,
        created_at=NOW,
    )


def _run(run_id: str, operation_id: str, proposal_count: int) -> DiscoveryRun:
    return DiscoveryRun(
        run_id=run_id,
        bid_id=BID_ID,
        operation_id=operation_id,
        status=RunStatus.SUCCEEDED,
        proposal_count=proposal_count,
        actor="Reviewer",
        started_at=NOW,
        completed_at=NOW + timedelta(minutes=1),
    )


def _consolidated(
    run_id: str,
    candidates: list[ProposedCandidate],
    units: list[ExtractionUnit],
) -> list[DeliverableProposal]:
    return _consolidate(
        BID_ID,
        run_id,
        NOW,
        candidates,
        {unit.extraction_id: unit for unit in units},
    )


def test_identical_title_different_obligations_survive_within_run_and_persist_collision(
    tmp_path: Path,
) -> None:
    database, repository = _repository(tmp_path)
    quote_a = "The Supplier shall submit a progress report every month after award."
    quote_b = "The Supplier shall provide an incident report after every safety event."
    units = [
        _unit("E-A", "RUN-A", quote_a, source_id="DOC-A", locator="p.8"),
        _unit("E-B", "RUN-A", quote_b, source_id="DOC-B", locator="p.9"),
    ]
    proposals = _consolidated(
        "RUN-A",
        [
            _candidate("E-A", quote_a, "Submit the monthly construction progress report."),
            _candidate("E-B", quote_b, "Report safety incidents after each event."),
        ],
        units,
    )

    assert len(proposals) == 2
    assert proposals[0].dedup_key.count(COLLISION_KEY_MARKER) == 0
    assert COLLISION_KEY_MARKER in proposals[1].dedup_key
    collisions = collisions_between(proposals)
    assert len(collisions) == 1 and collisions[0].reasons == ("description",)

    repository.create_run(
        _run("RUN-A", "within-run", 2),
        units,
        proposals,
        "Reviewer",
        collisions=collisions,
    )
    rows = repository.list_collisions(BID_ID)
    assert json.loads(rows[0]["reasons_json"]) == ["description"]
    with database._conn() as conn:
        audit = conn.execute(
            "SELECT 1 FROM audit_log WHERE action='deliverable_discovery_collision_recorded'"
        ).fetchone()
        assert audit is not None
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(f"UPDATE {COLLISIONS_TABLE} SET reasons_json='[]'")
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute(f"DELETE FROM {COLLISIONS_TABLE}")


def test_identical_obligations_merge_and_preserve_every_distinct_citation() -> None:
    quote = "The Supplier shall submit the monthly progress report within five days."
    units = [
        _unit("E-1", "RUN-I", quote, source_id="DOC-1", locator="p.2"),
        _unit("E-2", "RUN-I", quote, source_id="DOC-2", locator="p.17"),
    ]
    proposals = _consolidated(
        "RUN-I",
        [
            _candidate("E-1", quote, "Submit a monthly progress report within five days."),
            _candidate("E-2", quote, "Monthly progress report due within five days."),
        ],
        units,
    )

    assert len(proposals) == 1
    assert [(source.document_version_id, source.locator) for source in proposals[0].sources] == [
        ("DOC-1", "p.2"),
        ("DOC-2", "p.17"),
    ]
    assert collisions_between(proposals) == []


def test_vddr_rows_8_16_and_9_16_are_distinct_obligations() -> None:
    row_8 = "ROW 8.16 | DISC=MQ | DESCRIPTION=Statutory Authority Approval Certificates"
    row_9 = "ROW 9.16 | DESCRIPTION=Statutory Authority Approval Certificates"
    assert [item["source_section"] for item in interpret_vddr_unit(f"{row_8}\n{row_9}")] == [
        "Schedule of Vendor Drawing and Data Requirements, item 8.16",
        "Schedule of Vendor Drawing and Data Requirements, item 9.16",
    ]
    raw = RawUnit(
        locator_type="PAGE",
        locator="p.20 rows 8.16-9.16",
        text=f"{row_8}\n{row_9}",
        readable=True,
        kind="VDDR_TABLE",
    )
    candidates = list(analyse_units(_ForbiddenModel(), [("E-V", raw)]).candidates)
    unit = _unit("E-V", "RUN-V", raw.text, source_id="VDDR", locator=raw.locator)

    proposals = _consolidated("RUN-V", candidates, [unit])

    assert len(proposals) == 2
    assert {proposal.classification.source_section for proposal in proposals} == {
        "Schedule of Vendor Drawing and Data Requirements, item 8.16",
        "Schedule of Vendor Drawing and Data Requirements, item 9.16",
    }
    assert collisions_between(proposals)[0].reasons == ("source reference",)


def test_vddr_row_1_09_does_not_merge_with_same_titled_model_finding() -> None:
    row = (
        "ROW 1.09 | DISC=PM | DESCRIPTION=Monthly Progress Report | Required?=Y | "
        "REVIEW QTY=1 | REVIEW DUE=4W ARO | TIMING=Review 4W ARO"
    )
    raw = RawUnit(
        locator_type="PAGE",
        locator="p.3 row 1.09",
        text=row,
        readable=True,
        kind="VDDR_TABLE",
    )
    rule_candidate = analyse_units(_ForbiddenModel(), [("E-RULE", raw)]).candidates[0]
    model_quote = "The Supplier shall provide a Monthly Progress Report after each site visit."
    model_candidate = _candidate(
        "E-MODEL",
        model_quote,
        "Provide a report after every site visit.",
        title="Monthly Progress Report",
        timing="after each site visit",
        source_section="Contract clause 14",
    )
    units = [
        _unit("E-RULE", "RUN-M", row, source_id="VDDR", locator=raw.locator),
        _unit("E-MODEL", "RUN-M", model_quote, source_id="SPEC", locator="p.44"),
    ]

    proposals = _consolidated("RUN-M", [rule_candidate, model_candidate], units)

    assert len(proposals) == 2
    assert collisions_between(proposals)[0].reasons == ("timing", "source reference")


def test_cross_run_collision_stores_new_finding_without_changing_existing_key(
    tmp_path: Path,
) -> None:
    _, repository = _repository(tmp_path)
    quote_a = "The Supplier shall submit a monthly cost report."
    unit_a = _unit("E-OLD", "RUN-OLD", quote_a, source_id="DOC-OLD", locator="p.1")
    old = _consolidated(
        "RUN-OLD", [_candidate("E-OLD", quote_a, "Submit a monthly cost report.")], [unit_a]
    )[0]
    original_key = old.dedup_key
    repository.create_run(_run("RUN-OLD", "old", 1), [unit_a], [old], "Reviewer")

    quote_b = "The Supplier shall report environmental incidents within one day."
    unit_b = _unit("E-NEW", "RUN-NEW", quote_b, source_id="DOC-NEW", locator="p.7")
    found = _consolidated(
        "RUN-NEW",
        [_candidate("E-NEW", quote_b, "Report environmental incidents within one day.")],
        [unit_b],
    )
    split = split_known_duplicates(
        found,
        repository.existing_dedup_index(BID_ID),
        repository.existing_collision_index(BID_ID),
    )
    new, appends = split

    assert appends == [] and len(new) == 1 and len(split.collisions) == 1
    assert COLLISION_KEY_MARKER in new[0].dedup_key
    repository.create_run(
        _run("RUN-NEW", "new", 1),
        [unit_b],
        new,
        "Reviewer",
        collisions=split.collisions,
    )
    stored = {row["proposal_id"]: row for row in repository.list_proposals(BID_ID)}
    assert stored[old.proposal_id]["dedup_key"] == original_key
    assert len(stored) == 2 and len(repository.list_collisions(BID_ID)) == 1


def test_equivalent_cross_run_refinding_appends_citation_and_preserves_review(
    tmp_path: Path,
) -> None:
    _, repository = _repository(tmp_path)
    quote = "The Supplier shall submit the monthly progress report within five days."
    unit_a = _unit("E-A", "RUN-A", quote, source_id="DOC-A", locator="p.1")
    old = _consolidated(
        "RUN-A", [_candidate("E-A", quote, "Submit the monthly progress report.")], [unit_a]
    )[0]
    repository.create_run(_run("RUN-A", "first", 1), [unit_a], [old], "Reviewer")
    review = ProposalReviewEvent(
        proposal_id=old.proposal_id,
        bid_id=BID_ID,
        decision=ProposalReviewDecision.REJECTED,
        reviewer="Reviewer",
        rationale="Handled elsewhere",
        reviewed_at=NOW + timedelta(minutes=2),
    )
    repository.add_review_event(review, "Reviewer")
    before = repository.get_proposal(old.proposal_id)

    unit_b = _unit("E-B", "RUN-B", quote, source_id="DOC-B", locator="p.9")
    refound = _consolidated(
        "RUN-B",
        [_candidate("E-B", quote, "Monthly progress report submission.")],
        [unit_b],
    )
    split = split_known_duplicates(
        refound,
        repository.existing_dedup_index(BID_ID),
        repository.existing_collision_index(BID_ID),
    )
    new, appends = split
    assert new == [] and split.collisions == [] and appends[0][0] == old.proposal_id

    repository.create_run(_run("RUN-B", "second", 0), [unit_b], [], "Reviewer")
    repository.append_citations(appends[0][0], list(appends[0][1]))
    after = repository.get_proposal(old.proposal_id)
    assert after is not None and before is not None
    assert len(after["sources"]) == 2
    assert after["classification"] == before["classification"]
    assert repository.current_decision(old.proposal_id)["event_id"] == review.event_id  # type: ignore[index]


def test_description_overlap_threshold_is_documented_and_conservative() -> None:
    assert DESCRIPTION_TOKEN_OVERLAP_THRESHOLD == 0.75
    assert description_token_overlap("monthly progress report", "monthly progress report") == 1.0
    assert (
        description_token_overlap("monthly progress report", "environmental incident notice")
        < DESCRIPTION_TOKEN_OVERLAP_THRESHOLD
    )


def test_opening_existing_database_applies_collision_table_idempotently(tmp_path: Path) -> None:
    database, repository = _repository(tmp_path)
    assert repository.list_collisions(BID_ID) == []
    reopened = DeliverableDiscoveryRepository(database)
    assert reopened.list_collisions(BID_ID) == []
    with database._conn() as conn:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (COLLISIONS_TABLE,)
        ).fetchone()
    assert ddl is not None and "UNIQUE (proposal_id, other_proposal_id)" in ddl["sql"]
