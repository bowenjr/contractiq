from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.bid_package_intake import (
    AnalysisEligibility,
    ClassificationMethod,
    ContentForm,
    DirectiveCreate,
    DirectiveMateriality,
    FileDispositionCreate,
    SnapshotCreate,
)
from core.bid_repository import BidRepository
from core.deliverable_discovery import (
    DeliverableProposal,
    DiscoveryCriticality,
    DiscoveryRun,
    ExtractionStatus,
    ExtractionUnit,
    LocatorType,
    ProposalReviewDecision,
    RunStatus,
    SourceCitation,
    normalize_dedup_key,
)
from core.deliverable_discovery_repository import DeliverableDiscoveryRepository
from core.deliverable_discovery_service import (
    DeliverableDiscoveryService,
    DiscoveryUnavailableError,
    ProposalDecisionInput,
)
from core.deliverable_repository import DeliverableRepository
from core.deliverable_service import DeliverableService
from core.document_repository import DocumentRepository
from core.document_service import DocumentService
from core.managed_document_storage import ManagedDocumentStorage
from tests.unit.test_bid_package_intake import _review_and_link
from tests.unit.test_bid_package_storage import BID_ID, _command, _service
from tests.unit.test_deliverable_discovery_service import (
    _build_fixture,
    _pdf_bytes,
    _place_controlled_pdf,
    _StubLLM,
)


def _received_fixture(tmp_path: Path, *, classify: bool = True) -> dict[str, object]:
    package, package_repository, database, source, _managed = _service(tmp_path)
    contents = {
        "analysis.pdf": _pdf_bytes(
            ["Submit the signed Bid Form with the offer before the customer deadline please."]
        ),
        "governing.pdf": _pdf_bytes(
            ["A complete technical schedule must accompany every submission to procurement."]
        ),
        "not-assessed.pdf": _pdf_bytes(
            ["Submit another signed Bid Form which must never be read in this test package."]
        ),
        "excluded.pdf": _pdf_bytes(
            ["Submit an excluded signed Bid Form which must never be read in this test package."]
        ),
    }
    for name, content in contents.items():
        (source / name).write_bytes(content)
    preview = package.preview(BID_ID, "landing")
    release = package.register_release(_command(preview.source_fingerprint), "Jason")
    release_id = str(release["release_id"])
    rows = {
        str(row["original_filename"]): row
        for row in package.release_detail(BID_ID, release_id)["files"]
    }
    if classify:
        for index, (name, eligibility) in enumerate(
            (
                ("analysis.pdf", AnalysisEligibility.ELIGIBLE),
                ("governing.pdf", AnalysisEligibility.ELIGIBLE),
                ("excluded.pdf", AnalysisEligibility.EXCLUDED),
            )
        ):
            row = rows[name]
            package.classify_file(
                BID_ID,
                str(row["file_id"]),
                FileDispositionCreate(
                    content_form=ContentForm.TEXTUAL,
                    classification_method=ClassificationMethod.HUMAN_REVIEW,
                    confidence=1,
                    analysis_eligibility=eligibility,
                    exclusion_reason="Not Bid content."
                    if eligibility is AnalysisEligibility.EXCLUDED
                    else None,
                    supersedes_event_id=str(row["disposition_event_id"]),
                    operation_id=f"eligibility-{index}",
                ),
                "Jason",
            )
    documents = DocumentRepository(database)
    managed_documents = ManagedDocumentStorage(tmp_path / "managed-documents", 10_000_000)
    discovery_repository = DeliverableDiscoveryRepository(database)
    deliverables = DeliverableRepository(database)
    discovery = DeliverableDiscoveryService(
        discovery_repository,
        package_repository,
        DocumentService(documents, package.bid_repository, managed_documents),
        deliverables,
        DeliverableService(deliverables),
        _StubLLM(),
    )
    return {
        "package": package,
        "package_repository": package_repository,
        "database": database,
        "documents": documents,
        "managed_documents": managed_documents,
        "discovery_repository": discovery_repository,
        "deliverables": deliverables,
        "discovery": discovery,
        "release_id": release_id,
        "rows": rows,
        "contents": contents,
    }


def test_discovery_uses_only_current_eligible_received_files_before_basis(tmp_path: Path) -> None:
    fx = _received_fixture(tmp_path)
    reads: list[str] = []
    package = fx["package"]

    def tracked_open(bid_id: str, release_id: str, file_id: str):
        reads.append(file_id)
        return package.open_received_file(bid_id, release_id, file_id)

    fx["package_repository"].bind_received_file_opener(tracked_open)
    first = fx["discovery"].run_discovery(BID_ID, "received-op", "Jason")
    second = fx["discovery"].run_discovery(BID_ID, "received-op", "Jason")
    assert first["run_id"] == second["run_id"]
    assert first["snapshot_id"] is None
    assert first["proposal_count"] == 2
    assert set(reads) == {
        str(fx["rows"]["analysis.pdf"]["file_id"]),
        str(fx["rows"]["governing.pdf"]["file_id"]),
    }
    assert len(reads) == 2
    proposals = fx["discovery"].list_review_queue(BID_ID)
    assert len(proposals) == 2
    for proposal in proposals:
        source = proposal["sources"][0]
        assert source["document_version_id"] is None
        assert source["received_file_id"] in reads
        assert source["locator"] == "p.1"
        assert source["excerpt"]
    with fx["database"]._conn() as conn:
        assert conn.execute("SELECT count(*) FROM bid_basis_snapshots").fetchone()[0] == 0
        assert (
            conn.execute("SELECT count(*) FROM bid_received_file_document_links").fetchone()[0] == 0
        )


def test_no_eligible_received_file_is_unavailable(tmp_path: Path) -> None:
    fx = _received_fixture(tmp_path, classify=False)
    with pytest.raises(DiscoveryUnavailableError, match="Include for analysis"):
        fx["discovery"].run_discovery(BID_ID, "none", "Jason")


def test_source_identity_and_import_source_cache_guards(tmp_path: Path) -> None:
    fx = _received_fixture(tmp_path)
    fx["discovery"].run_discovery(BID_ID, "source-op", "Jason")
    package = fx["discovery"].export_package(BID_ID)
    citation = package.proposals[0].sources[0]
    assert citation.received_file_id and citation.document_version_id is None
    with pytest.raises(ValidationError, match="exactly one"):
        SourceCitation(locator_type=LocatorType.PAGE, locator="p.1", excerpt="x")
    with pytest.raises(ValidationError, match="exactly one"):
        SourceCitation(
            document_version_id="DV-X",
            received_file_id="BRF-X",
            locator_type=LocatorType.PAGE,
            locator="p.1",
            excerpt="x",
        )
    with fx["database"]._conn() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "INSERT INTO deliverable_discovery_proposal_sources_v2 VALUES (?,?,?,?,?,?,?)",
                (
                    "DDC-invalid-xor",
                    fx["discovery"].list_review_queue(BID_ID)[0]["proposal_id"],
                    "DV-X",
                    "RF-X",
                    "PAGE",
                    "p.1",
                    "x",
                ),
            )

    bids = BidRepository(fx["database"])
    original = bids.get_bid(BID_ID)
    assert original is not None
    other_bid_id = "B-2026-0012"
    bids.create_bid(original.model_copy(update={"bid_id": other_bid_id}))
    preview = fx["package"].preview(other_bid_id, "landing")
    release = fx["package"].register_release(
        _command(preview.source_fingerprint, "other-register").model_copy(
            update={"bid_id": other_bid_id}
        ),
        "Jason",
    )
    detail = fx["package"].release_detail(other_bid_id, str(release["release_id"]))
    other_row = detail["files"][0]
    fx["package"].classify_file(
        other_bid_id,
        str(other_row["file_id"]),
        FileDispositionCreate(
            content_form=ContentForm.TEXTUAL,
            classification_method=ClassificationMethod.HUMAN_REVIEW,
            confidence=1,
            analysis_eligibility=AnalysisEligibility.ELIGIBLE,
            supersedes_event_id=str(other_row["disposition_event_id"]),
            operation_id="other-eligible",
        ),
        "Jason",
    )
    fx["discovery"].run_discovery(other_bid_id, "other-run", "Jason")
    other_source = fx["discovery"].list_review_queue(other_bid_id)[0]["sources"][0]
    tampered = package.model_copy(
        update={
            "proposals": tuple(
                proposal.model_copy(
                    update={
                        "title": f"Imported {index}",
                        "sources": tuple(
                            source.model_copy(
                                update={
                                    "received_file_id": (
                                        "RF-fabricated"
                                        if index == 0
                                        else other_source["received_file_id"]
                                    )
                                }
                            )
                            for source in proposal.sources
                        ),
                    }
                )
                for index, proposal in enumerate(package.proposals)
            )
        }
    )
    result = fx["discovery"].import_package(
        BID_ID, tampered.model_dump_json(), "cross-source", "Jason"
    )
    assert result["proposal_count"] == 0
    assert result["rejected_source_count"] == len(package.proposals)


def test_received_file_acceptance_is_atomic_and_human_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _received_fixture(tmp_path)
    fx["discovery"].run_discovery(BID_ID, "accept-run", "Jason")
    proposals = fx["discovery"].list_review_queue(BID_ID)
    accepted = fx["discovery"].decide(
        BID_ID,
        [ProposalDecisionInput(proposals[0]["proposal_id"], ProposalReviewDecision.ACCEPTED)],
        "Jason",
    )[0]
    deliverable_id = accepted["deliverable_id"]
    row = next(
        item for item in fx["deliverables"].list(BID_ID) if item["deliverable_id"] == deliverable_id
    )
    assert row["workflow_state"] == "ACTIVE"
    provenance = json.loads(row["provenance_json"])
    assert provenance["created_by"] == "human"
    assert provenance["human_confirmed"] is True
    link = fx["deliverables"].links(deliverable_id)[0]
    assert (link["target_type"], link["relation"]) == ("RECEIVED_FILE", "SOURCE_RECEIVED_FILE")
    assert (
        fx["discovery_repository"].current_decision(proposals[0]["proposal_id"])["decision"]
        == "ACCEPTED"
    )

    original_audit = fx["discovery_repository"]._audit

    def fail_review_audit(conn, bid_id, actor, action, detail):
        if action == "deliverable_discovery_proposal_reviewed":
            raise RuntimeError("synthetic audit failure")
        original_audit(conn, bid_id, actor, action, detail)

    monkeypatch.setattr(fx["discovery_repository"], "_audit", fail_review_audit)
    rolled_back = fx["discovery"].decide(
        BID_ID,
        [ProposalDecisionInput(proposals[1]["proposal_id"], ProposalReviewDecision.ACCEPTED)],
        "Jason",
    )[0]
    assert "synthetic audit failure" in rolled_back["error"]
    assert len(fx["deliverables"].list(BID_ID)) == 1
    assert fx["discovery_repository"].current_decision(proposals[1]["proposal_id"]) is None


def test_pre_basis_discovery_survives_later_governing_publication(tmp_path: Path) -> None:
    fx = _received_fixture(tmp_path)
    run = fx["discovery"].run_discovery(BID_ID, "before-basis", "Jason")
    proposal_ids = [row["proposal_id"] for row in fx["discovery"].list_review_queue(BID_ID)]
    accepted = fx["discovery"].decide(
        BID_ID,
        [ProposalDecisionInput(proposal_ids[0], ProposalReviewDecision.ACCEPTED)],
        "Jason",
    )[0]
    deliverable_before = next(
        row
        for row in fx["deliverables"].list(BID_ID)
        if row["deliverable_id"] == accepted["deliverable_id"]
    )
    not_assessed = fx["rows"]["not-assessed.pdf"]
    fx["package"].classify_file(
        BID_ID,
        str(not_assessed["file_id"]),
        FileDispositionCreate(
            content_form=ContentForm.TEXTUAL,
            classification_method=ClassificationMethod.HUMAN_REVIEW,
            confidence=1,
            analysis_eligibility=AnalysisEligibility.EXCLUDED,
            exclusion_reason="Not needed for the published basis.",
            supersedes_event_id=str(not_assessed["disposition_event_id"]),
            operation_id="finish-review-before-basis",
        ),
        "Jason",
    )
    content = fx["contents"]["analysis.pdf"]
    version_id = _place_controlled_pdf(
        fx["documents"], fx["managed_documents"], content, suffix="000000000099"
    )
    _review_and_link(fx["package"], fx["package_repository"], fx["release_id"], version_id)
    snapshot = fx["package"].publish_basis(
        BID_ID,
        SnapshotCreate(
            release_ids=(fx["release_id"],), label="Later basis", operation_id="later-basis"
        ),
        "Jason",
    )
    assert snapshot["snapshot_id"]
    assert (
        fx["discovery_repository"].existing_run(BID_ID, "before-basis")["run_id"] == run["run_id"]
    )
    assert [row["proposal_id"] for row in fx["discovery"].list_review_queue(BID_ID)] == proposal_ids
    assert (
        next(
            row
            for row in fx["deliverables"].list(BID_ID)
            if row["deliverable_id"] == accepted["deliverable_id"]
        )
        == deliverable_before
    )


def test_document_version_sources_and_populated_migration_remain_supported(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    now = datetime.now(UTC)
    run = DiscoveryRun(
        bid_id=BID_ID,
        snapshot_id=fx["snapshot"]["snapshot_id"],
        operation_id="legacy-source",
        status=RunStatus.SUCCEEDED,
        proposal_count=1,
        actor="Jason",
        started_at=now,
        completed_at=now,
    )
    unit = ExtractionUnit(
        bid_id=BID_ID,
        run_id=run.run_id,
        document_version_id=fx["version_id"],
        locator_type=LocatorType.PAGE,
        locator="p.1",
        text_excerpt="Submit the signed legacy bid form with the offer.",
        char_count=50,
        status=ExtractionStatus.OK,
        created_at=now,
    )
    proposal = DeliverableProposal(
        bid_id=BID_ID,
        run_id=run.run_id,
        title="Legacy Document Source",
        category="FORM",
        criticality=DiscoveryCriticality.REQUIRED,
        sources=(
            SourceCitation(
                document_version_id=fx["version_id"],
                locator_type=LocatorType.PAGE,
                locator="p.1",
                excerpt=unit.text_excerpt,
            ),
        ),
        dedup_key=normalize_dedup_key(BID_ID, "Legacy Document Source", "FORM"),
        created_at=now,
    )
    fx["discovery_repository"].create_run(run, [unit], [proposal], "Jason")
    result = fx["discovery_service"].decide(
        BID_ID,
        [ProposalDecisionInput(proposal.proposal_id, ProposalReviewDecision.ACCEPTED)],
        "Jason",
    )[0]
    link = fx["deliverable_repository"].links(result["deliverable_id"])[0]
    assert (link["target_type"], link["relation"]) == (
        "DOCUMENT_VERSION",
        "SOURCE_DOCUMENT_VERSION",
    )
    fx["package_service"].record_directive(
        BID_ID,
        fx["release_id"],
        DirectiveCreate(
            directive_type="CHANGE",
            description="Legacy source change.",
            materiality=DirectiveMateriality.MATERIAL,
            target_document_version_id=fx["version_id"],
            operation_id="legacy-source-directive",
        ),
        "Jason",
    )
    assert set(fx["discovery_service"].detect_addendum_impacts(BID_ID, "Jason")) == {
        proposal.proposal_id,
        result["deliverable_id"],
    }

    with fx["discovery_repository"]._conn() as conn:
        conn.execute(
            "INSERT INTO deliverable_discovery_extractions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "DDX-v1-populated",
                BID_ID,
                run.run_id,
                fx["version_id"],
                "PAGE",
                "p.2",
                "Legacy populated extraction",
                27,
                "OK",
                None,
                now.isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO deliverable_discovery_proposal_sources VALUES (?,?,?,?,?,?)",
            (
                "DDC-v1-populated",
                proposal.proposal_id,
                fx["version_id"],
                "PAGE",
                "p.2",
                "Legacy populated extraction",
            ),
        )
    DeliverableDiscoveryRepository(fx["discovery_repository"].db)
    DeliverableDiscoveryRepository(fx["discovery_repository"].db)
    with fx["discovery_repository"]._conn() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM deliverable_discovery_extractions_v2 "
                "WHERE extraction_id='DDX-v1-populated'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM deliverable_discovery_proposal_sources_v2 "
                "WHERE citation_id='DDC-v1-populated'"
            ).fetchone()[0]
            == 1
        )
