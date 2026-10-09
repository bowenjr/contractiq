"""Full-pipeline OPS-13A service tests against a real (synthetic) Bid, intake release, controlled
document version, and published Bid Basis snapshot — no real customer package files are used or
committed; every document is built in-memory/tmp_path at test time.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

from core.bid_package_intake import (
    DirectiveCreate,
    DirectiveMateriality,
    SnapshotCreate,
)
from core.deliverable_discovery import ProposalReviewDecision
from core.deliverable_discovery_repository import DeliverableDiscoveryRepository
from core.deliverable_discovery_service import (
    DeliverableDiscoveryService,
    ImportRejectedError,
    ProposalDecisionInput,
)
from core.deliverable_repository import DeliverableRepository
from core.deliverable_service import DeliverableService
from core.document_repository import DocumentRepository
from core.document_service import DocumentService
from core.managed_document_storage import ManagedDocumentStorage
from core.schemas import Provenance
from tests.unit.test_bid_package_intake import _review_and_link
from tests.unit.test_bid_package_storage import BID_ID, NOW, _command, _service


class _StubLLM:
    """Deterministic stand-in for the local LLM: proposes one candidate per distinct passage."""

    def complete_json(
        self,
        prompt,
        system_prompt="",
        temperature=0.05,
        max_tokens=1024,
        context_label=None,
        timeout=None,
    ):
        if "Bid Form" in prompt:
            return {
                "candidates": [
                    {
                        "title": "Signed Bid Form",
                        "category": "FORM",
                        "criticality": "REQUIRED",
                        "condition_text": None,
                        "due_date": None,
                        "requested_format": None,
                        "requested_recipient": "Procurement",
                        "proposed_owner": None,
                    }
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
                        "requested_format": None,
                        "requested_recipient": None,
                        "proposed_owner": None,
                    }
                ]
            }
        return {"candidates": []}


def _place_controlled_pdf(
    document_repository: DocumentRepository,
    storage: ManagedDocumentStorage,
    content: bytes,
    *,
    suffix: str,
) -> str:
    """Insert a controlled document + CURRENT version using the real
    ManagedDocumentStorage storage-key convention, and physically place the bytes, so
    DocumentService.open_download() can actually read it back — mirrors what
    DocumentService.register_document() does internally, without going through its full
    multi-form intake flow (which OPS-13A does not touch)."""
    document_id = f"DOC-00000000-0000-0000-0000-{suffix}"
    version_id = f"DV-00000000-0000-0000-0000-{suffix}"
    provenance = Provenance.from_human("Jason").model_dump_json()
    storage_key = ManagedDocumentStorage.storage_key(version_id)
    path = storage.resolve(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with document_repository._conn() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """INSERT INTO documents(id,filename,bid_id,control_managed,control_title,
            document_number,document_category,control_lifecycle,current_version_id,
            control_created_at,control_updated_at,control_version,control_provenance_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                document_id,
                "instructions.pdf",
                BID_ID,
                1,
                "Synthetic Bid Instructions",
                "SYN-001",
                "SOLICITATION",
                "ACTIVE",
                version_id,
                NOW.isoformat(),
                NOW.isoformat(),
                1,
                provenance,
            ),
        )
        conn.execute(
            """INSERT INTO document_versions(document_version_id,document_id,version_label,
            issued_date,received_at,original_filename,media_type,byte_size,sha256_digest,
            storage_key,predecessor_version_id,version_state,created_at,provenance_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                version_id,
                document_id,
                "A",
                date(2026, 9, 10).isoformat(),
                NOW.isoformat(),
                "instructions.pdf",
                "application/pdf",
                len(content),
                hashlib.sha256(content).hexdigest(),
                storage_key,
                None,
                "CURRENT",
                NOW.isoformat(),
                provenance,
            ),
        )
    return version_id


def _pdf_bytes(pages: list[str]) -> bytes:
    import fitz

    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _build_fixture(tmp_path: Path):
    package_service, package_repository, database, source, _managed = _service(tmp_path)
    document_repository = DocumentRepository(database)
    managed_storage = ManagedDocumentStorage(tmp_path / "managed_docs", 10_000_000)
    document_service = DocumentService(
        document_repository, package_service.bid_repository, managed_storage
    )

    content = _pdf_bytes(
        [
            "Submit the completed signed Bid Form with your offer, signed by an authorized "
            "company representative before the submission deadline.",
            "This technical specification requires a complete technical schedule to accompany "
            "every submission made against this solicitation package.",
        ]
    )
    version_id = _place_controlled_pdf(
        document_repository, managed_storage, content, suffix="000000000021"
    )

    (source / "controlled.pdf").write_bytes(content)
    preview = package_service.preview(BID_ID, "landing")
    release = package_service.register_release(_command(preview.source_fingerprint), "Jason")
    release_id = str(release["release_id"])
    file_id = _review_and_link(package_service, package_repository, release_id, version_id)

    snapshot = package_service.publish_basis(
        BID_ID,
        SnapshotCreate(release_ids=(release_id,), label="Bid Basis 1", operation_id="basis-1"),
        "Jason",
    )

    deliverable_repository = DeliverableRepository(database)
    deliverable_service = DeliverableService(deliverable_repository)
    discovery_repository = DeliverableDiscoveryRepository(database)
    discovery_service = DeliverableDiscoveryService(
        discovery_repository,
        package_repository,
        document_service,
        deliverable_repository,
        deliverable_service,
        _StubLLM(),
    )
    return {
        "discovery_service": discovery_service,
        "discovery_repository": discovery_repository,
        "deliverable_repository": deliverable_repository,
        "package_service": package_service,
        "package_repository": package_repository,
        "version_id": version_id,
        "release_id": release_id,
        "file_id": file_id,
        "snapshot": snapshot,
    }


def test_run_discovery_extracts_and_consolidates_proposals(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    result = fx["discovery_service"].run_discovery(BID_ID, "op-1", "Jason")
    assert result["proposal_count"] == 2

    proposals = fx["discovery_service"].list_review_queue(BID_ID)
    titles = {p["title"] for p in proposals}
    assert titles == {"Signed Bid Form", "Technical Schedule"}
    for proposal in proposals:
        assert proposal["sources"]
        for source in proposal["sources"]:
            assert source["document_version_id"] is None
            assert source["received_file_id"] == fx["file_id"]


def test_run_discovery_is_idempotent_on_operation_id(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    first = fx["discovery_service"].run_discovery(BID_ID, "op-replay", "Jason")
    second = fx["discovery_service"].run_discovery(BID_ID, "op-replay", "Jason")
    assert first["run_id"] == second["run_id"]
    assert len(fx["discovery_service"].list_review_queue(BID_ID)) == 2


def test_accept_creates_authoritative_deliverable_matching_readiness_filter(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    fx["discovery_service"].run_discovery(BID_ID, "op-1", "Jason")
    proposal = fx["discovery_service"].list_review_queue(BID_ID)[0]

    results = fx["discovery_service"].decide(
        BID_ID,
        [
            ProposalDecisionInput(
                proposal_id=proposal["proposal_id"], decision=ProposalReviewDecision.ACCEPTED
            )
        ],
        "Jason",
    )
    assert "error" not in results[0]
    deliverable_id = results[0]["deliverable_id"]
    assert deliverable_id

    rows = fx["deliverable_repository"].list(BID_ID)
    created = next(r for r in rows if r["deliverable_id"] == deliverable_id)
    # Exact filter proposal_exchange_service.py uses to pick up Bid-submission deliverables —
    # confirming reuse works without touching that module.
    assert created["workflow_state"] != "CANCELLED"
    assert created["lifecycle_phase"] in ("WITH_BID", "PRE_AWARD", "BOTH")
    assert created["direction"] in ("COMPANY_TO_CUSTOMER", "SUPPLIER_TO_CUSTOMER_VIA_COMPANY")
    assert created["workflow_state"] == "ACTIVE"


def test_reject_and_defer_never_create_a_deliverable(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    fx["discovery_service"].run_discovery(BID_ID, "op-1", "Jason")
    proposals = fx["discovery_service"].list_review_queue(BID_ID)

    fx["discovery_service"].decide(
        BID_ID,
        [
            ProposalDecisionInput(
                proposal_id=proposals[0]["proposal_id"],
                decision=ProposalReviewDecision.REJECTED,
                rationale="Not actually required for this Bid.",
            ),
            ProposalDecisionInput(
                proposal_id=proposals[1]["proposal_id"],
                decision=ProposalReviewDecision.DEFERRED,
                rationale="Need to confirm with engineering first.",
            ),
        ],
        "Jason",
    )
    assert fx["deliverable_repository"].list(BID_ID) == []
    queue = fx["discovery_service"].list_review_queue(BID_ID)
    decisions = {p["proposal_id"]: p["current_decision"] for p in queue}
    assert decisions[proposals[0]["proposal_id"]] == "REJECTED"
    assert decisions[proposals[1]["proposal_id"]] == "DEFERRED"


def test_addendum_matches_received_file_sourced_proposals_via_current_version_link(
    tmp_path: Path,
) -> None:
    fx = _build_fixture(tmp_path)
    fx["discovery_service"].run_discovery(BID_ID, "op-1", "Jason")
    proposals_before = fx["discovery_service"].list_review_queue(BID_ID)

    fx["package_service"].record_directive(
        BID_ID,
        fx["release_id"],
        DirectiveCreate(
            directive_type="CHANGE",
            description="Revised submission requirement.",
            materiality=DirectiveMateriality.MATERIAL,
            target_document_version_id=fx["version_id"],
            operation_id="directive-1",
        ),
        "Jason",
    )
    flagged = fx["discovery_service"].detect_addendum_impacts(BID_ID, "Jason")
    assert set(flagged) == {row["proposal_id"] for row in proposals_before}

    for proposal_before in proposals_before:
        proposal_after = fx["discovery_repository"].get_proposal(proposal_before["proposal_id"])
        assert proposal_after["title"] == proposal_before["title"]  # unchanged, only flagged

    flags = fx["discovery_repository"].list_addendum_flags(BID_ID)
    assert len(flags) == len(proposals_before)
    assert all(fx["file_id"] in row["rationale"] for row in flags)
    assert all(fx["version_id"] in row["rationale"] for row in flags)

    # calling again is idempotent — no duplicate flags for the same directive/target
    flagged_again = fx["discovery_service"].detect_addendum_impacts(BID_ID, "Jason")
    assert flagged_again == []
    assert fx["discovery_repository"].list_addendum_flags(BID_ID) == flags


def test_export_then_import_validates_sources_and_skips_existing_duplicates(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    fx["discovery_service"].run_discovery(BID_ID, "op-1", "Jason")
    package = fx["discovery_service"].export_package(BID_ID)
    assert package.discovery_outcome == "SUCCEEDED"

    # Re-importing byte-identical content: every citation is genuine evidence already in this
    # Bid's own extraction cache (0 rejected), but both proposals already exist under the same
    # dedup_key from the original run, so reimport/replay creates no duplicate proposals.
    result = fx["discovery_service"].import_package(
        BID_ID, package.model_dump_json(), "import-op-1", "Jason"
    )
    assert result["rejected_source_count"] == 0
    assert result["proposal_count"] == 0
    assert len(fx["discovery_service"].list_review_queue(BID_ID)) == 2

    # A genuinely new title (different dedup_key) with valid sources imports as one new
    # proposal, proving the skip above was about the duplicate, not a blanket rejection.
    retitled = package.model_copy(
        update={
            "proposals": (
                package.proposals[0].model_copy(update={"title": "Countersigned Bid Form Copy"}),
            )
        }
    )
    second = fx["discovery_service"].import_package(
        BID_ID, retitled.model_dump_json(), "import-op-2", "Jason"
    )
    assert second["rejected_source_count"] == 0
    assert second["proposal_count"] == 1
    assert len(fx["discovery_service"].list_review_queue(BID_ID)) == 3


def test_import_rejects_fabricated_or_cross_bid_source_citations(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    fx["discovery_service"].run_discovery(BID_ID, "op-1", "Jason")
    package = fx["discovery_service"].export_package(BID_ID)
    tampered = package.model_copy(
        update={
            "proposals": tuple(
                p.model_copy(
                    update={
                        "sources": tuple(
                            s.model_copy(update={"locator": "p.999-does-not-exist"})
                            for s in p.sources
                        )
                    }
                )
                for p in package.proposals
            )
        }
    )
    result = fx["discovery_service"].import_package(
        BID_ID, tampered.model_dump_json(), "import-op-2", "Jason"
    )
    assert result["proposal_count"] == 0
    assert result["rejected_source_count"] == 2


def test_import_rejects_mismatched_bid_id(tmp_path: Path) -> None:
    fx = _build_fixture(tmp_path)
    fx["discovery_service"].run_discovery(BID_ID, "op-1", "Jason")
    package = fx["discovery_service"].export_package(BID_ID)
    cross_bid = package.model_dump_json().replace(BID_ID, "B-OTHER-BID")
    with pytest.raises(ImportRejectedError, match="cross-Bid"):
        fx["discovery_service"].import_package(BID_ID, cross_bid, "import-op-3", "Jason")
