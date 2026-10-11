"""Atomic citation append regressions, mirroring the Stage 2 temp-database repository pattern."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

import core.deliverable_discovery_job_repository as job_repository_module
import core.deliverable_discovery_repository as repository_module
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
from core.deliverable_discovery_job_repository import DeliverableDiscoveryJobRepository
from core.deliverable_discovery_outcomes import DiscoveryOutcome, DiscoveryRunOutcome
from core.deliverable_discovery_repository import DeliverableDiscoveryRepository
from tests.unit.test_stage2_classification_repository import (
    BID_ID,
    NOW,
    _classification,
    _create_proposal,
    _repository,
)


def _run_payload(
    operation_id: str,
) -> tuple[DiscoveryRun, ExtractionUnit, DeliverableProposal, SourceCitation]:
    run = DiscoveryRun(
        bid_id=BID_ID,
        operation_id=operation_id,
        status=RunStatus.SUCCEEDED,
        proposal_count=1,
        actor="Reviewer",
        started_at=NOW,
        completed_at=NOW + timedelta(minutes=1),
    )
    source_id = f"DV-{operation_id}"
    locator = f"p.{operation_id}"
    passage = "The Bidder shall submit the signed form with its tender."
    extraction = ExtractionUnit(
        bid_id=BID_ID,
        run_id=run.run_id,
        document_version_id=source_id,
        locator_type=LocatorType.PAGE,
        locator=locator,
        text_excerpt=passage,
        char_count=len(passage),
        status=ExtractionStatus.OK,
        created_at=NOW,
    )
    citation = SourceCitation(
        document_version_id=source_id,
        locator_type=LocatorType.PAGE,
        locator=locator,
        excerpt="caller text is replaced from the stored extraction",
        quote="the bidder SHALL submit the signed form with its tender.",
    )
    proposal_citation = SourceCitation(
        document_version_id=source_id,
        locator_type=LocatorType.PAGE,
        locator=locator,
        excerpt=passage,
        quote=passage,
    )
    title = f"New proposal {operation_id}"
    classification = _classification()
    proposal = DeliverableProposal(
        bid_id=BID_ID,
        run_id=run.run_id,
        title=title,
        category="Form",
        criticality=DiscoveryCriticality.REQUIRED,
        classification=classification,
        sources=(proposal_citation,),
        dedup_key=normalize_dedup_key(BID_ID, title, "Form", classification),
        created_at=NOW,
    )
    return run, extraction, proposal, citation


def _source_count(repository: DeliverableDiscoveryRepository, proposal_id: str) -> int:
    proposal = repository.get_proposal(proposal_id)
    assert proposal is not None
    return len(proposal["sources"])


def _run_outcome(run_id: str) -> DiscoveryRunOutcome:
    return DiscoveryRunOutcome(
        run_id=run_id,
        outcome=DiscoveryOutcome.SUCCEEDED,
        files_included=0,
        files_unsupported=0,
        files_unreadable=0,
        files_extracted=0,
        files_analysed=0,
        files_failed=0,
        passages_total=0,
        model_calls=0,
        passages_analysed=0,
        passages_model_failed=0,
        passages_invalid_output=0,
    )


def _seed_running_job(
    repository: DeliverableDiscoveryRepository, operation_id: str, owner: str
) -> tuple[DeliverableDiscoveryJobRepository, str]:
    jobs = DeliverableDiscoveryJobRepository(repository.db)
    job_id = f"JOB-{operation_id}"
    with repository._conn() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """INSERT INTO deliverable_discovery_jobs(
            job_id,bid_id,operation_id,actor,state,force_all,lease_owner,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (job_id, BID_ID, operation_id, "Reviewer", "RUNNING", 0, owner, NOW.isoformat()),
        )
    return jobs, job_id


def test_create_run_commits_new_proposal_and_cross_run_append(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    existing = _create_proposal(repository, classification=_classification())
    run, extraction, proposal, citation = _run_payload("atomic-success")

    repository.create_run(
        run,
        [extraction],
        [proposal],
        "Reviewer",
        appends=[(existing.proposal_id, (citation,))],
    )

    assert repository.existing_run(BID_ID, run.operation_id) is not None
    assert repository.get_proposal(proposal.proposal_id) is not None
    stored = repository.get_proposal(existing.proposal_id)
    assert stored is not None and len(stored["sources"]) == 2
    assert stored["sources"][1]["quote"] == (
        "The Bidder shall submit the signed form with its tender."
    )


def test_create_run_append_failure_rolls_back_and_same_operation_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    existing = _create_proposal(repository, classification=_classification())
    run, extraction, proposal, citation = _run_payload("atomic-retry")
    original = repository_module.append_citations_in_transaction

    def fail_append(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected append failure")

    monkeypatch.setattr(repository_module, "append_citations_in_transaction", fail_append)
    with pytest.raises(RuntimeError, match="injected append failure"):
        repository.create_run(
            run,
            [extraction],
            [proposal],
            "Reviewer",
            appends=[(existing.proposal_id, (citation,))],
        )

    assert repository.existing_run(BID_ID, run.operation_id) is None
    assert repository.get_proposal(proposal.proposal_id) is None
    assert _source_count(repository, existing.proposal_id) == 1

    monkeypatch.setattr(repository_module, "append_citations_in_transaction", original)
    repository.create_run(
        run,
        [extraction],
        [proposal],
        "Reviewer",
        appends=[(existing.proposal_id, (citation,))],
    )

    with repository._conn() as conn:  # type: ignore[attr-defined]
        assert (
            conn.execute(
                "SELECT count(*) FROM deliverable_discovery_runs WHERE operation_id=?",
                (run.operation_id,),
            ).fetchone()[0]
            == 1
        )
    assert _source_count(repository, existing.proposal_id) == 2


def test_job_finalize_append_failure_rolls_back_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    existing = _create_proposal(repository, classification=_classification())
    owner = "test-owner"
    jobs, job_id = _seed_running_job(repository, "job-atomic-retry", owner)
    run, extraction, proposal, citation = _run_payload("job-atomic-retry")
    original = job_repository_module.append_citations_in_transaction

    def fail_append(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected job append failure")

    monkeypatch.setattr(job_repository_module, "append_citations_in_transaction", fail_append)
    with pytest.raises(RuntimeError, match="injected job append failure"):
        jobs.finalize_run(
            job_id,
            owner,
            run,
            [extraction],
            [proposal],
            _run_outcome(run.run_id),
            [],
            False,
            appends=[(existing.proposal_id, (citation,))],
        )

    assert repository.existing_run(BID_ID, run.operation_id) is None
    assert repository.get_proposal(proposal.proposal_id) is None
    assert _source_count(repository, existing.proposal_id) == 1
    assert jobs.get(job_id)["state"] == "RUNNING"  # type: ignore[index]

    monkeypatch.setattr(job_repository_module, "append_citations_in_transaction", original)
    assert jobs.finalize_run(
        job_id,
        owner,
        run,
        [extraction],
        [proposal],
        _run_outcome(run.run_id),
        [],
        False,
        appends=[(existing.proposal_id, (citation,))],
    )
    assert _source_count(repository, existing.proposal_id) == 2
    assert (
        jobs.finalize_run(
            job_id,
            owner,
            run,
            [extraction],
            [proposal],
            _run_outcome(run.run_id),
            [],
            False,
            appends=[(existing.proposal_id, (citation,))],
        )
        is False
    )
    assert _source_count(repository, existing.proposal_id) == 2


def test_public_append_citations_still_runs_in_its_own_transaction(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    existing = _create_proposal(repository, classification=_classification())
    run, extraction, _proposal, citation = _run_payload("standalone")
    empty_run = run.model_copy(update={"proposal_count": 0})
    repository.create_run(empty_run, [extraction], [], "Reviewer")

    repository.append_citations(existing.proposal_id, [citation])
    repository.append_citations(existing.proposal_id, [citation])

    assert _source_count(repository, existing.proposal_id) == 2


def test_atomic_append_does_not_change_review_or_classification(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    existing = _create_proposal(repository, classification=_classification())
    event = ProposalReviewEvent(
        proposal_id=existing.proposal_id,
        bid_id=BID_ID,
        decision=ProposalReviewDecision.REJECTED,
        reviewer="Reviewer",
        rationale="Not applicable",
        reviewed_at=NOW,
    )
    repository.add_review_event(event, "Reviewer")
    before = repository.get_proposal(existing.proposal_id)
    run, extraction, _proposal, citation = _run_payload("reviewed")
    empty_run = run.model_copy(update={"proposal_count": 0})

    repository.create_run(
        empty_run,
        [extraction],
        [],
        "Reviewer",
        appends=[(existing.proposal_id, (citation,))],
    )

    after = repository.get_proposal(existing.proposal_id)
    assert before is not None and after is not None
    assert after["classification"] == before["classification"]
    assert after["review_events"] == before["review_events"]
    assert repository.current_decision(existing.proposal_id)["decision"] == "REJECTED"  # type: ignore[index]
    assert len(after["sources"]) == 2
