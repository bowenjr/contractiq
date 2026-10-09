from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from core.bid_package_intake import (
    AnalysisEligibility,
    ClassificationMethod,
    ContentForm,
    DirectiveCreate,
    DirectiveMateriality,
    FileDispositionCreate,
    FileDocumentLinkCreate,
    FileDocumentRelationship,
    ReleaseType,
    SnapshotCreate,
)
from core.deliverable_discovery import ProposalReviewDecision
from core.deliverable_discovery_repository import DeliverableDiscoveryRepository
from core.deliverable_discovery_service import (
    DeliverableDiscoveryService,
    ProposalDecisionInput,
)
from core.deliverable_repository import DeliverableRepository
from core.deliverable_service import DeliverableService
from core.document_repository import DocumentRepository
from core.document_service import DocumentService
from core.managed_document_storage import ManagedDocumentStorage
from scripts.asgi_acceptance_ops08 import request
from tests.unit.test_bid_package_governing_basis import (
    _select_governing,
    _snapshot_documents,
)
from tests.unit.test_bid_package_storage import BID_ID, _command, _service
from tests.unit.test_deliverable_discovery_extraction import _docx_bytes, _xlsx_bytes
from tests.unit.test_deliverable_discovery_service import _pdf_bytes, _place_controlled_pdf


def test_deliverable_discovery_end_to_end_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timings: list[tuple[int, str, float]] = []

    class _WorkflowLLM:
        model_id = "synthetic-workflow-llm"

        def complete_json(self, prompt: str, **_kwargs: object) -> dict[str, object]:
            if "signed Bid Form" in prompt:
                return {
                    "candidates": [
                        {
                            "title": "Signed Bid Form",
                            "category": "FORM",
                            "criticality": "REQUIRED",
                            "condition_text": None,
                            "due_date": None,
                            "requested_format": "PDF",
                            "requested_recipient": "Procurement",
                            "proposed_owner": None,
                        },
                        {
                            "title": "Bid Bond",
                            "category": "SECURITY",
                            "criticality": "REQUIRED",
                            "condition_text": None,
                            "due_date": None,
                            "requested_format": None,
                            "requested_recipient": "Procurement",
                            "proposed_owner": None,
                        },
                        {
                            "title": "Safety Plan",
                            "category": "HSE",
                            "criticality": "REQUIRED",
                            "condition_text": None,
                            "due_date": None,
                            "requested_format": None,
                            "requested_recipient": "Procurement",
                            "proposed_owner": None,
                        },
                    ]
                }
            if "technical schedule" in prompt:
                return {
                    "candidates": [
                        {
                            "title": "Technical Schedule",
                            "category": "TECHNICAL",
                            "criticality": "REQUIRED",
                            "condition_text": None,
                            "due_date": None,
                            "requested_format": "DOCX",
                            "requested_recipient": None,
                            "proposed_owner": None,
                        }
                    ]
                }
            if "pricing workbook" in prompt:
                return {
                    "candidates": [
                        {
                            "title": "Pricing Workbook",
                            "category": "COMMERCIAL",
                            "criticality": "REQUIRED",
                            "condition_text": None,
                            "due_date": None,
                            "requested_format": "XLSX",
                            "requested_recipient": "Procurement",
                            "proposed_owner": None,
                        }
                    ]
                }
            return {"candidates": []}

    # 1. RECEIVE a mixed initial package.
    started = time.perf_counter()
    package, package_repository, database, source, _managed = _service(tmp_path)
    contents = {
        "bid-instructions.pdf": _pdf_bytes(
            [
                "The Bid submission must include the signed Bid Form, a valid Bid Bond, and "
                "the required Safety Plan, all delivered to Procurement with the complete "
                "offer before the customer deadline."
            ]
        ),
        "technical-requirements.docx": _docx_bytes(
            [
                "The Bid submission must include the complete technical schedule for every "
                "offered item and submit it with the proposal to Procurement."
            ]
        ),
        "commercial-returnables.xlsx": _xlsx_bytes(
            [
                ["Required Bid submission", "Instruction"],
                [
                    "Pricing",
                    "The supplier must submit the completed pricing workbook with the Bid "
                    "submission to Procurement.",
                ],
            ]
        ),
        "corrupt.pdf": b"random truncated bytes, not a real PDF\x00\xff",
        "drawing.dwg": b"synthetic unsupported drawing bytes",
    }
    for filename, content in contents.items():
        (source / filename).write_bytes(content)
    preview = package.preview(BID_ID, "landing")
    release = package.register_release(
        _command(preview.source_fingerprint, "e2e-register-initial"), "Jason"
    )
    release_id = str(release["release_id"])
    rows = {
        str(row["original_filename"]): row
        for row in package.release_detail(BID_ID, release_id)["files"]
    }
    assert set(rows) == set(contents)
    assert release["release_type"] == ReleaseType.INITIAL_PACKAGE.value
    timings.append((1, "receive mixed package", time.perf_counter() - started))

    # 2. MARK FILES FOR ANALYSIS, including corrupt and unsupported extension paths.
    started = time.perf_counter()
    for index, row in enumerate(rows.values()):
        package.classify_file(
            BID_ID,
            str(row["file_id"]),
            FileDispositionCreate(
                content_form=ContentForm.TEXTUAL,
                classification_method=ClassificationMethod.HUMAN_REVIEW,
                confidence=1,
                analysis_eligibility=AnalysisEligibility.ELIGIBLE,
                supersedes_event_id=str(row["disposition_event_id"]),
                operation_id=f"e2e-eligible-{index}",
            ),
            "Jason",
        )
    reviewed_rows = {
        str(row["original_filename"]): row
        for row in package.release_detail(BID_ID, release_id)["files"]
    }
    assert {row["analysis_eligibility"] for row in reviewed_rows.values()} == {"ELIGIBLE"}
    timings.append((2, "mark files eligible", time.perf_counter() - started))

    # 3. RUN DISCOVERY before a Bid Basis exists.
    started = time.perf_counter()
    assert package_repository.current_snapshot(BID_ID) is None
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
        _WorkflowLLM(),
    )
    run = discovery.run_discovery(BID_ID, "e2e-discovery", "Jason")
    assert run["status"] == "SUCCEEDED"
    assert run["snapshot_id"] is None
    proposals = discovery.list_review_queue(BID_ID)
    assert run["proposal_count"] == len(proposals)
    assert len(proposals) >= 2
    good_filenames = {
        "bid-instructions.pdf",
        "technical-requirements.docx",
        "commercial-returnables.xlsx",
    }
    good_file_ids = {str(reviewed_rows[name]["file_id"]) for name in good_filenames}
    proposal_sources = [source_row for row in proposals for source_row in row["sources"]]
    assert {str(source_row["received_file_id"]) for source_row in proposal_sources} == (
        good_file_ids
    )
    assert all(source_row["document_version_id"] is None for source_row in proposal_sources)
    assert all(source_row["received_file_id"] for source_row in proposal_sources)
    assert all(source_row["locator"] for source_row in proposal_sources)

    corrupt_file_id = str(reviewed_rows["corrupt.pdf"]["file_id"])
    unsupported_file_id = str(reviewed_rows["drawing.dwg"]["file_id"])
    unreadable = discovery_repository.list_unreadable_extractions(BID_ID)
    assert len(unreadable) == 1
    assert unreadable[0]["received_file_id"] == corrupt_file_id
    assert unreadable[0]["original_filename"] == "corrupt.pdf"
    with database._conn() as conn:  # type: ignore[attr-defined]
        corrupt_extraction = dict(
            conn.execute(
                "SELECT * FROM deliverable_discovery_extractions_v2 WHERE received_file_id=?",
                (corrupt_file_id,),
            ).fetchone()
        )
        unsupported_extractions = conn.execute(
            "SELECT count(*) FROM deliverable_discovery_extractions_v2 WHERE received_file_id=?",
            (unsupported_file_id,),
        ).fetchone()[0]
    assert corrupt_extraction["status"] == "UNREADABLE"
    assert unsupported_extractions == 0
    assert all(source_row["received_file_id"] != corrupt_file_id for source_row in proposal_sources)
    assert all(
        source_row["received_file_id"] != unsupported_file_id for source_row in proposal_sources
    )

    import app as app_module

    monkeypatch.setattr(app_module, "bid_repository", package.bid_repository)
    monkeypatch.setattr(app_module, "deliverable_discovery_service", discovery)
    monkeypatch.setattr(app_module, "deliverable_discovery_repository", discovery_repository)
    status, _, body = asyncio.run(
        request(app_module.app, "GET", f"/bids/{BID_ID}/deliverable-discovery")
    )
    assert status == 200
    assert b"Files that could not be read" in body
    assert b"corrupt.pdf" in body
    assert b"These files were NOT analysed" in body
    timings.append((3, "pre-basis discovery", time.perf_counter() - started))

    # 4. ACCEPT one proposal, REJECT another from the same file, and leave a third pending.
    started = time.perf_counter()
    proposals_by_title = {str(row["title"]): row for row in proposals}
    accepted_proposal = proposals_by_title["Signed Bid Form"]
    rejected_proposal = proposals_by_title["Bid Bond"]
    pending_proposal = proposals_by_title["Safety Plan"]
    pdf_file_id = str(reviewed_rows["bid-instructions.pdf"]["file_id"])
    assert {
        accepted_proposal["sources"][0]["received_file_id"],
        rejected_proposal["sources"][0]["received_file_id"],
        pending_proposal["sources"][0]["received_file_id"],
    } == {pdf_file_id}
    accepted_result = discovery.decide(
        BID_ID,
        [
            ProposalDecisionInput(
                str(accepted_proposal["proposal_id"]), ProposalReviewDecision.ACCEPTED
            )
        ],
        "Jason",
    )[0]
    assert "error" not in accepted_result
    deliverable_id = str(accepted_result["deliverable_id"])
    rejected_result = discovery.decide(
        BID_ID,
        [
            ProposalDecisionInput(
                str(rejected_proposal["proposal_id"]),
                ProposalReviewDecision.REJECTED,
                rationale="Confirmed duplicate returnable for this synthetic review.",
            )
        ],
        "Jason",
    )[0]
    assert "error" not in rejected_result
    accepted_deliverable = next(
        row for row in deliverables.list(BID_ID) if row["deliverable_id"] == deliverable_id
    )
    assert accepted_deliverable["workflow_state"] == "ACTIVE"
    received_link = deliverables.links(deliverable_id)[0]
    assert (
        received_link["target_type"],
        received_link["target_id"],
        received_link["relation"],
    ) == ("RECEIVED_FILE", pdf_file_id, "SOURCE_RECEIVED_FILE")
    assert (
        discovery_repository.current_decision(str(accepted_proposal["proposal_id"]))["decision"]
        == "ACCEPTED"
    )
    assert (
        discovery_repository.current_decision(str(rejected_proposal["proposal_id"]))["decision"]
        == "REJECTED"
    )
    assert discovery_repository.current_decision(str(pending_proposal["proposal_id"])) is None
    timings.append((4, "review proposals", time.perf_counter() - started))

    # 5. SELECT the three good received files as governing sources.
    started = time.perf_counter()
    good_rows = [reviewed_rows[name] for name in sorted(good_filenames)]
    _select_governing(
        package,
        package_repository,
        release_id,
        good_rows,
        operation_id="e2e-select-governing",
    )
    for row in good_rows:
        assert (
            package_repository.current_governing_disposition(str(row["file_id"]))[
                "governing_disposition"
            ]
            == "GOVERNING"
        )
    assert (
        package_repository.current_governing_disposition(corrupt_file_id)["governing_disposition"]
        == "UNSET"
    )
    assert (
        package_repository.current_governing_disposition(unsupported_file_id)[
            "governing_disposition"
        ]
        == "UNSET"
    )
    timings.append((5, "select governing", time.perf_counter() - started))

    # 6. LINK exact controlled versions and PROMOTE the accepted citation.
    started = time.perf_counter()
    versions_by_filename: dict[str, str] = {}
    file_links: dict[str, dict[str, object]] = {}
    for index, filename in enumerate(sorted(good_filenames), start=1):
        version_id = _place_controlled_pdf(
            documents,
            managed_documents,
            contents[filename],
            suffix=f"0000000004{index:02d}",
        )
        versions_by_filename[filename] = version_id
        link = package.link_file_document(
            BID_ID,
            str(reviewed_rows[filename]["file_id"]),
            FileDocumentLinkCreate(
                document_version_id=version_id,
                relationship=FileDocumentRelationship.EXACT_BYTES,
                operation_id=f"e2e-link-{index}",
            ),
            "Jason",
        )
        assert link["relationship"] == "EXACT_BYTES"
        assert link["document_version_id"] == version_id
        file_links[filename] = link

    original_citation = dict(accepted_proposal["sources"][0])
    original_received_link = dict(received_link)
    pdf_version_id = versions_by_filename["bid-instructions.pdf"]
    promotion = discovery.promote_source_to_controlled_version(
        BID_ID,
        deliverable_id,
        str(original_citation["citation_id"]),
        pdf_version_id,
        "Exact controlled copy registered from the received package.",
        "e2e-promote-source",
        "Jason",
    )
    persisted_citation = next(
        source_row
        for source_row in discovery_repository.get_proposal(str(accepted_proposal["proposal_id"]))[
            "sources"
        ]
        if source_row["citation_id"] == original_citation["citation_id"]
    )
    assert persisted_citation == original_citation
    promoted_links = deliverables.links(deliverable_id)
    assert original_received_link in promoted_links
    assert {(row["target_type"], row["target_id"], row["relation"]) for row in promoted_links} == {
        ("RECEIVED_FILE", pdf_file_id, "SOURCE_RECEIVED_FILE"),
        ("DOCUMENT_VERSION", pdf_version_id, "SOURCE_DOCUMENT_VERSION"),
    }
    promotions = discovery_repository.list_promotions(BID_ID)
    assert len(promotions) == 1
    assert promotions[0]["promotion_id"] == promotion.promotion_id
    assert (
        promotions[0]["file_document_link_id"]
        == file_links["bid-instructions.pdf"]["file_document_link_id"]
    )
    with database._conn() as conn:  # type: ignore[attr-defined]
        promotion_actions = {
            row["action"]
            for row in conn.execute(
                "SELECT action FROM audit_log WHERE action IN (?,?)",
                ("deliverable_discovery_source_promoted", "deliverable_link_created"),
            ).fetchall()
        }
    assert promotion_actions == {
        "deliverable_discovery_source_promoted",
        "deliverable_link_created",
    }
    timings.append((6, "link and promote", time.perf_counter() - started))

    # 7. PUBLISH the first Bid Basis snapshot from the governing controlled versions.
    started = time.perf_counter()
    snapshot = package.publish_basis(
        BID_ID,
        SnapshotCreate(
            release_ids=(release_id,),
            label="OPS-13A synthetic E2E Bid Basis",
            operation_id="e2e-publish-basis",
        ),
        "Jason",
    )
    current_snapshot = package_repository.current_snapshot(BID_ID)
    assert current_snapshot == snapshot
    snapshot_documents = _snapshot_documents(database, str(snapshot["snapshot_id"]))
    assert {row["document_version_id"] for row in snapshot_documents} == set(
        versions_by_filename.values()
    )
    assert {row["source_received_file_id"] for row in snapshot_documents} == good_file_ids
    for row in snapshot_documents:
        assert (
            package_repository.current_governing_disposition(str(row["source_received_file_id"]))[
                "governing_disposition"
            ]
            == "GOVERNING"
        )
    timings.append((7, "publish bid basis", time.perf_counter() - started))

    # Snapshot immutable business rows before receiving the addendum.
    accepted_row_before = json.dumps(
        next(
            dict(row)
            for row in deliverables.list(BID_ID)
            if row["deliverable_id"] == deliverable_id
        ),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    accepted_proposal_before = discovery_repository.get_proposal(
        str(accepted_proposal["proposal_id"])
    )
    review_events_before = json.dumps(
        accepted_proposal_before["review_events"],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    original_citation_before = json.dumps(
        next(
            row
            for row in accepted_proposal_before["sources"]
            if row["citation_id"] == original_citation["citation_id"]
        ),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    # 8. RECEIVE an ADDENDUM and record a MATERIAL directive against the controlled version.
    started = time.perf_counter()
    for path in source.iterdir():
        path.unlink()
    (source / "addendum-01.pdf").write_bytes(
        _pdf_bytes(
            [
                "Addendum 01 changes the required Bid submission instructions for the signed "
                "Bid Form, Bid Bond, and Safety Plan supplied with the offer."
            ]
        )
    )
    addendum_preview = package.preview(BID_ID, "landing")
    addendum = package.register_release(
        _command(addendum_preview.source_fingerprint, "e2e-register-addendum").model_copy(
            update={
                "release_type": ReleaseType.ADDENDUM,
                "exact_customer_reference": "ADDENDUM-01",
            }
        ),
        "Jason",
    )
    addendum_release_id = str(addendum["release_id"])
    assert addendum["release_type"] == "ADDENDUM"
    directive = package.record_directive(
        BID_ID,
        addendum_release_id,
        DirectiveCreate(
            directive_type="CHANGE",
            description="Revise the Bid submission returnables required by Addendum 01.",
            materiality=DirectiveMateriality.MATERIAL,
            target_document_version_id=pdf_version_id,
            operation_id="e2e-material-directive",
        ),
        "Jason",
    )
    assert directive["materiality"] == "MATERIAL"
    assert directive["target_document_version_id"] == pdf_version_id
    timings.append((8, "receive addendum", time.perf_counter() - started))

    # 9. VERIFY IMPACT without mutating the authoritative item, review, or citation.
    started = time.perf_counter()
    flagged = discovery.detect_addendum_impacts(BID_ID, "Jason")
    accepted_proposal_id = str(accepted_proposal["proposal_id"])
    rejected_proposal_id = str(rejected_proposal["proposal_id"])
    pending_proposal_id = str(pending_proposal["proposal_id"])
    expected_flagged = {
        accepted_proposal_id,
        rejected_proposal_id,
        pending_proposal_id,
        deliverable_id,
    }
    assert set(flagged) == expected_flagged
    flags = discovery_repository.list_addendum_flags(BID_ID)
    assert len(flags) == len(expected_flagged)
    assert {str(row["proposal_id"] or row["deliverable_id"]) for row in flags} == expected_flagged
    assert {row["directive_id"] for row in flags} == {directive["directive_id"]}
    proposal_flags = {str(row["proposal_id"]): row for row in flags if row["proposal_id"]}
    assert {accepted_proposal_id, rejected_proposal_id, pending_proposal_id}.issubset(
        proposal_flags
    )
    for proposal_id in (accepted_proposal_id, rejected_proposal_id, pending_proposal_id):
        rationale = str(proposal_flags[proposal_id]["rationale"])
        assert str(directive["directive_id"]) in rationale
        assert pdf_version_id in rationale
        assert pdf_file_id in rationale
    deliverable_flag = next(row for row in flags if row["deliverable_id"] == deliverable_id)
    rationale_defects = []
    deliverable_rationale = str(deliverable_flag["rationale"])
    if str(directive["directive_id"]) not in deliverable_rationale:
        rationale_defects.append("accepted deliverable rationale omits the directive ID")
    if pdf_version_id not in deliverable_rationale:
        rationale_defects.append("accepted deliverable rationale omits the controlled version ID")

    accepted_row_after = json.dumps(
        next(
            dict(row)
            for row in deliverables.list(BID_ID)
            if row["deliverable_id"] == deliverable_id
        ),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    accepted_proposal_after = discovery_repository.get_proposal(accepted_proposal_id)
    review_events_after = json.dumps(
        accepted_proposal_after["review_events"],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    original_citation_after = json.dumps(
        next(
            row
            for row in accepted_proposal_after["sources"]
            if row["citation_id"] == original_citation["citation_id"]
        ),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert accepted_row_after == accepted_row_before
    assert review_events_after == review_events_before
    assert original_citation_after == original_citation_before
    assert discovery_repository.current_decision(rejected_proposal_id)["decision"] == "REJECTED"
    assert discovery_repository.current_decision(pending_proposal_id) is None
    assert discovery.detect_addendum_impacts(BID_ID, "Jason") == []
    assert discovery_repository.list_addendum_flags(BID_ID) == flags

    status, _, flagged_page = asyncio.run(
        request(app_module.app, "GET", f"/bids/{BID_ID}/deliverable-discovery")
    )
    assert status == 200
    assert b"Flagged for re-review after an addendum" in flagged_page
    for flag in flags:
        assert str(flag["rationale"]).encode() in flagged_page
    timings.append((9, "detect addendum impact", time.perf_counter() - started))

    # 10. VERIFY unreadable/citation/audit evidence remains intact.
    started = time.perf_counter()
    final_unreadable = discovery_repository.list_unreadable_extractions(BID_ID)
    assert len(final_unreadable) == 1
    assert final_unreadable[0]["received_file_id"] == corrupt_file_id
    assert final_unreadable[0]["original_filename"] == "corrupt.pdf"
    final_proposals = discovery.list_review_queue(BID_ID)
    assert all(
        source_row["received_file_id"] != corrupt_file_id
        for proposal in final_proposals
        for source_row in proposal["sources"]
    )
    with database._conn() as conn:  # type: ignore[attr-defined]
        audit_actions = {
            str(row["action"])
            for row in conn.execute(
                "SELECT action FROM audit_log WHERE bid_id=?",
                (BID_ID,),
            ).fetchall()
        }
    assert {
        "deliverable_discovery_run_completed",
        "deliverable_discovery_proposal_reviewed",
        "deliverable_discovery_source_promoted",
        "deliverable_discovery_addendum_flag_recorded",
    }.issubset(audit_actions)
    timings.append((10, "final evidence checks", time.perf_counter() - started))

    print("\nOPS-13A synthetic E2E step timings")
    print("step  workflow                       seconds")
    for step, label, seconds in timings:
        print(f"{step:>4}  {label:<29} {seconds:>7.4f}")

    assert not rationale_defects, "; ".join(rationale_defects)
