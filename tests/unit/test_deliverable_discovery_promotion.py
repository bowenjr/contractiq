from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.bid_package_intake import FileDocumentLinkCreate, FileDocumentRelationship
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
from core.deliverable_discovery_service import ProposalDecisionInput
from scripts.asgi_acceptance_ops08 import request
from tests.unit.test_bid_package_storage import BID_ID
from tests.unit.test_deliverable_discovery_received_files import _received_fixture
from tests.unit.test_deliverable_discovery_service import _place_controlled_pdf


def _accepted(tmp_path: Path) -> dict[str, object]:
    fx = _received_fixture(tmp_path)
    fx["discovery"].run_discovery(BID_ID, "promotion-run", "Jason")
    proposal = next(
        row
        for row in fx["discovery"].list_review_queue(BID_ID)
        if row["title"] == "Signed Bid Form"
    )
    accepted = fx["discovery"].decide(
        BID_ID,
        [ProposalDecisionInput(proposal["proposal_id"], ProposalReviewDecision.ACCEPTED)],
        "Jason",
    )[0]
    return {
        **fx,
        "proposal": proposal,
        "citation": proposal["sources"][0],
        "deliverable_id": accepted["deliverable_id"],
    }


def _version(fx: dict[str, object], suffix: str = "000000000201") -> str:
    return _place_controlled_pdf(
        fx["documents"],
        fx["managed_documents"],
        fx["contents"]["analysis.pdf"],
        suffix=suffix,
    )


def _link(
    fx: dict[str, object],
    version_id: str,
    relationship: FileDocumentRelationship = FileDocumentRelationship.EXACT_BYTES,
) -> dict[str, object]:
    citation = fx["citation"]
    return fx["package"].link_file_document(
        BID_ID,
        citation["received_file_id"],
        FileDocumentLinkCreate(
            document_version_id=version_id,
            relationship=relationship,
            operation_id=f"file-link-{relationship.value}",
        ),
        "Jason",
    )


def _promote(fx: dict[str, object], version_id: str, operation_id: str = "promote-1"):
    return fx["discovery"].promote_source_to_controlled_version(
        BID_ID,
        fx["deliverable_id"],
        fx["citation"]["citation_id"],
        version_id,
        "The controlled copy is now available.",
        operation_id,
        "Jason",
    )


def test_happy_path_preserves_received_source_and_adds_audited_version_link(
    tmp_path: Path,
) -> None:
    fx = _accepted(tmp_path)
    version_id = _version(fx)
    file_link = _link(fx, version_id)
    original_citation = dict(fx["citation"])
    original_link = fx["deliverables"].links(fx["deliverable_id"])[0]

    promotion = _promote(fx, version_id)

    persisted_citation = next(
        source
        for proposal in fx["discovery"].list_review_queue(BID_ID)
        for source in proposal["sources"]
        if source["citation_id"] == original_citation["citation_id"]
    )
    assert dict(persisted_citation) == original_citation
    links = fx["deliverables"].links(fx["deliverable_id"])
    assert original_link in links
    assert {(row["target_type"], row["relation"]) for row in links} == {
        ("RECEIVED_FILE", "SOURCE_RECEIVED_FILE"),
        ("DOCUMENT_VERSION", "SOURCE_DOCUMENT_VERSION"),
    }
    rows = fx["discovery_repository"].list_promotions(BID_ID)
    assert len(rows) == 1
    assert rows[0]["promotion_id"] == promotion.promotion_id
    assert rows[0]["file_document_link_id"] == file_link["file_document_link_id"]
    with fx["database"]._conn() as conn:
        actions = {
            row["action"]
            for row in conn.execute(
                "SELECT action FROM audit_log WHERE action IN (?,?)",
                ("deliverable_discovery_source_promoted", "deliverable_link_created"),
            ).fetchall()
        }
    assert actions == {"deliverable_discovery_source_promoted", "deliverable_link_created"}


def test_rejects_missing_or_unsupported_file_document_link(tmp_path: Path) -> None:
    fx = _accepted(tmp_path)
    version_id = _version(fx)
    with pytest.raises(ValueError, match="first link.*Package Intake"):
        _promote(fx, version_id)
    _link(fx, version_id, FileDocumentRelationship.SUPPORTING_EVIDENCE)
    with pytest.raises(ValueError, match="SUPPORTING_EVIDENCE.*Package Intake"):
        _promote(fx, version_id)


def test_rejects_mismatched_version_and_cross_bid(tmp_path: Path) -> None:
    fx = _accepted(tmp_path)
    linked_version = _version(fx)
    other_version = _version(fx, "000000000202")
    _link(fx, linked_version)
    with pytest.raises(ValueError, match="does not match the current Package Intake link"):
        _promote(fx, other_version)
    with pytest.raises(ValueError, match="belongs to another Bid"):
        fx["discovery"].promote_source_to_controlled_version(
            "B-OTHER",
            fx["deliverable_id"],
            fx["citation"]["citation_id"],
            linked_version,
            "Wrong Bid.",
            "cross-bid",
            "Jason",
        )


def test_rejects_nonaccepted_and_document_version_citations(tmp_path: Path) -> None:
    fx = _received_fixture(tmp_path)
    fx["discovery"].run_discovery(BID_ID, "pending-run", "Jason")
    pending = fx["discovery"].list_review_queue(BID_ID)[0]
    with pytest.raises(ValueError, match="not currently accepted"):
        fx["discovery_repository"].promote_source(
            bid_id=BID_ID,
            deliverable_id="DEL-missing",
            citation_id=pending["sources"][0]["citation_id"],
            document_version_id="DV-missing",
            rationale="Not accepted.",
            operation_id="pending-promotion",
            actor="Jason",
        )

    version_id = _version(fx)
    now = datetime.now(UTC)
    run = DiscoveryRun(
        bid_id=BID_ID,
        operation_id="document-citation-run",
        status=RunStatus.SUCCEEDED,
        proposal_count=1,
        actor="Jason",
        started_at=now,
        completed_at=now,
    )
    unit = ExtractionUnit(
        bid_id=BID_ID,
        run_id=run.run_id,
        document_version_id=version_id,
        locator_type=LocatorType.PAGE,
        locator="p.1",
        text_excerpt="Submit document source.",
        char_count=23,
        status=ExtractionStatus.OK,
        created_at=now,
    )
    proposal = DeliverableProposal(
        bid_id=BID_ID,
        run_id=run.run_id,
        title="Document Source",
        category="FORM",
        criticality=DiscoveryCriticality.REQUIRED,
        sources=(
            SourceCitation(
                document_version_id=version_id,
                locator_type=LocatorType.PAGE,
                locator="p.1",
                excerpt="Submit document source.",
            ),
        ),
        dedup_key=normalize_dedup_key(BID_ID, "Document Source", "FORM"),
        created_at=now,
    )
    fx["discovery_repository"].create_run(run, [unit], [proposal], "Jason")
    result = fx["discovery"].decide(
        BID_ID,
        [ProposalDecisionInput(proposal.proposal_id, ProposalReviewDecision.ACCEPTED)],
        "Jason",
    )[0]
    with pytest.raises(ValueError, match="already names a controlled document version"):
        fx["discovery_repository"].promote_source(
            bid_id=BID_ID,
            deliverable_id=result["deliverable_id"],
            citation_id=proposal.sources[0].citation_id,
            document_version_id=version_id,
            rationale="Already controlled.",
            operation_id="document-promotion",
            actor="Jason",
        )


def test_replay_is_idempotent_and_second_operation_is_rejected(tmp_path: Path) -> None:
    fx = _accepted(tmp_path)
    version_id = _version(fx)
    _link(fx, version_id)
    first = _promote(fx, version_id)
    replay = _promote(fx, version_id)
    assert replay == first
    assert len(fx["discovery_repository"].list_promotions(BID_ID)) == 1
    assert len(fx["deliverables"].links(fx["deliverable_id"])) == 2
    with pytest.raises(ValueError, match="already promoted"):
        _promote(fx, version_id, "promote-2")


def test_failure_rolls_back_and_promotion_rows_are_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _accepted(tmp_path)
    version_id = _version(fx)
    _link(fx, version_id)
    original_audit = fx["discovery_repository"]._audit

    def fail_link_audit(conn, bid_id, actor, action, detail):
        if action == "deliverable_link_created":
            raise RuntimeError("synthetic promotion audit failure")
        original_audit(conn, bid_id, actor, action, detail)

    monkeypatch.setattr(fx["discovery_repository"], "_audit", fail_link_audit)
    with pytest.raises(RuntimeError, match="synthetic promotion audit failure"):
        _promote(fx, version_id)
    assert fx["discovery_repository"].list_promotions(BID_ID) == []
    assert len(fx["deliverables"].links(fx["deliverable_id"])) == 1

    monkeypatch.setattr(fx["discovery_repository"], "_audit", original_audit)
    promotion = _promote(fx, version_id, "promotion-after-rollback")
    with fx["database"]._conn() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE deliverable_discovery_source_promotions SET rationale='changed' "
                "WHERE promotion_id=?",
                (promotion.promotion_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute(
                "DELETE FROM deliverable_discovery_source_promotions WHERE promotion_id=?",
                (promotion.promotion_id,),
            )


def test_route_returns_422_for_guard_failure_and_200_for_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app as app_module

    fx = _accepted(tmp_path)
    version_id = _version(fx)
    monkeypatch.setattr(app_module, "bid_repository", fx["package"].bid_repository)
    monkeypatch.setattr(app_module, "deliverable_discovery_service", fx["discovery"])
    monkeypatch.setattr(app_module, "deliverable_discovery_repository", fx["discovery_repository"])
    form = {
        "deliverable_id": fx["deliverable_id"],
        "citation_id": fx["citation"]["citation_id"],
        "document_version_id": version_id,
        "rationale": "Controlled copy confirmed.",
        "operation_id": "route-before-link",
    }
    status, _, body = asyncio.run(
        request(
            app_module.app,
            "POST",
            f"/bids/{BID_ID}/deliverable-discovery/promote-source",
            form,
        )
    )
    assert status == 422
    assert b"first link" in body

    _link(fx, version_id)
    form["operation_id"] = "route-after-link"
    status, _, body = asyncio.run(
        request(
            app_module.app,
            "POST",
            f"/bids/{BID_ID}/deliverable-discovery/promote-source",
            form,
        )
    )
    assert status == 200
    assert b"Source promoted to controlled version" in body
