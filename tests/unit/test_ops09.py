from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from core.approval_repository import ApprovalRepository
from core.bid_repository import BidRepository
from core.commercial_repository import CommercialRepository
from core.contract_risk_repository import ContractRiskRepository
from core.database import Database
from core.deliverable_repository import DeliverableRepository
from core.document_repository import DocumentRepository
from core.enums import BidLevel, BidStatus, CustomerType, Gate
from core.ops07 import Ops07Repository
from core.ops08 import Ops08Repository
from core.proposal_exchange import CustomerIssueCommand, ProposalControlStatus
from core.proposal_exchange_contract import (
    CONTRACT_ROOT,
    ExchangeContractError,
    canonical_sha256,
    strict_json_loads,
    validate_manifest,
    validate_package,
)
from core.proposal_exchange_service import (
    ProposalExchangeNotFoundError,
    ProposalExchangeService,
    ProposalImportError,
    StaleProposalCandidateError,
)
from core.proposal_repository import ProposalRepository
from core.requirement_repository import RequirementRepository
from core.scenario_repository import ScenarioRepository
from core.schemas import Approval, Bid, Provenance
from core.scope_repository import ScopeInterfaceRepository
from core.vendor_document_repository import VendorDocumentRepository

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
BID_ID = "B-2026-0909"


def service_with_ready_bid(tmp_path: Path) -> tuple[ProposalExchangeService, BidRepository]:
    db = Database(tmp_path / "ops09.db")
    bid_repository = BidRepository(db)
    DocumentRepository(db)
    RequirementRepository(db)
    ScopeInterfaceRepository(db)
    VendorDocumentRepository(db)
    Ops07Repository(db)
    CommercialRepository(db)
    ops08 = Ops08Repository(db)
    ops08.bootstrap_topics()
    ContractRiskRepository(db)
    ApprovalRepository(db)
    ScenarioRepository(db)
    ProposalRepository(db)
    DeliverableRepository(db)
    service = ProposalExchangeService(
        db,
        bid_repository,
        tmp_path / "artifacts",
        now_factory=lambda: NOW,
    )
    seed_ready_bid(db, bid_repository)
    return service, bid_repository


def seed_ready_bid(db: Database, bid_repository: BidRepository, bid_id: str = BID_ID) -> None:
    provenance = Provenance.from_human("Jason").model_dump_json()
    bid_repository.create_bid(
        Bid(
            bid_id=bid_id,
            customer="Example EPCM",
            customer_type=CustomerType.EPCM,
            project_name="North Plant Electrical Package",
            location="Toronto, Canada",
            sales_owner="Jason",
            bc_owner="Jason",
            release_date=date(2026, 9, 1),
            customer_due_date=date(2026, 10, 1),
            internal_due_date=date(2026, 9, 25),
            estimated_value=Decimal("1000000"),
            classification=BidLevel.LEVEL_0,
            current_gate=Gate.G5,
            status=BidStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    suffix = bid_id[-4:]
    stable_uuid = f"00000000-0000-0000-0000-00000000{suffix}"
    document_id = f"DOC-{stable_uuid}"
    document_version_id = f"DV-{stable_uuid}"
    requirement_id = f"REQ-{stable_uuid}"
    scope_id = f"SCOPE-{suffix}"
    position_id = f"CP-{suffix}"
    position_version_id = f"CPV-{suffix}"
    scenario_family_id = f"SCF-{suffix}"
    scenario_version_id = f"SCV-{suffix}"
    with db._conn() as conn:
        conn.execute(
            """INSERT INTO documents(id,filename,bid_id,control_managed,control_title,
            document_category,control_lifecycle,current_version_id,control_created_at,
            control_updated_at,control_version,control_provenance_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                document_id,
                "customer-rfp.pdf",
                bid_id,
                1,
                "Customer RFP",
                "SOLICITATION",
                "ACTIVE",
                document_version_id,
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
                document_version_id,
                document_id,
                "0",
                "2026-09-01",
                NOW.isoformat(),
                "customer-rfp.pdf",
                "application/pdf",
                4,
                "a" * 64,
                f"documents/{suffix}/customer-rfp.pdf",
                None,
                "CURRENT",
                NOW.isoformat(),
                provenance,
            ),
        )
        conn.execute(
            """INSERT INTO requirements(requirement_id,bid_id,title,statement,origin,category,
            significance,lifecycle_stage,lifecycle_state,owner,source_document_id,
            source_document_version_id,source_clause,disposition,response_text,work_state,
            review_state,reviewer,created_at,updated_at,version,provenance_json,contributor)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                requirement_id,
                bid_id,
                "Supply equipment",
                "Supply the electrical equipment package.",
                "EXPLICIT",
                "TECHNICAL",
                "MANDATORY",
                "BID",
                "ACTIVE",
                "Jason",
                document_id,
                document_version_id,
                "1.1",
                "COMPLY",
                "We will supply the required equipment package.",
                "COMPLETE",
                "ACCEPTED",
                "Independent reviewer",
                NOW.isoformat(),
                NOW.isoformat(),
                1,
                provenance,
                "Engineering",
            ),
        )
        conn.execute(
            """INSERT INTO scope_interface_items(scope_item_id,bid_id,title,description,
            scope_area,origin,customer_need,offer_position,pricing_state,responsible_party,owner,
            materiality,evidence_decision_note,work_state,review_state,reviewer,review_note,
            lifecycle_state,created_at,updated_at,version,provenance_json,created_by)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                scope_id,
                bid_id,
                "Equipment supply",
                "Supply the electrical equipment package.",
                "CORE_PRODUCTS",
                "REQUIREMENT_DERIVED",
                "REQUIRED",
                "INCLUDED",
                "PRICED",
                "ContractIQ team",
                "Jason",
                "MATERIAL",
                "Confirmed scope basis",
                "COMPLETE",
                "ACCEPTED",
                "Independent reviewer",
                "Accepted",
                "ACTIVE",
                NOW.isoformat(),
                NOW.isoformat(),
                1,
                provenance,
                "Jason",
            ),
        )
        topic = conn.execute(
            "SELECT topic_id,current_version FROM commercial_topics ORDER BY topic_id LIMIT 1"
        ).fetchone()
        assert topic is not None
        topic_version = conn.execute(
            "SELECT topic_version_id FROM commercial_topic_versions "
            "WHERE topic_id=? AND version_number=?",
            (topic["topic_id"], topic["current_version"]),
        ).fetchone()
        assert topic_version is not None
        conn.execute(
            "INSERT INTO commercial_positions VALUES(?,?,?,?,?,?)",
            (position_id, bid_id, topic["topic_id"], 1, NOW.isoformat(), "Jason"),
        )
        conn.execute(
            """INSERT INTO commercial_position_versions(position_version_id,position_id,bid_id,
            topic_version_id,version_number,customer_position,proposed_position,disposition,
            rationale,owner,contributor,reviewer,negotiation_state,supporting_evidence,
            current_outcome,provenance_json,created_at,created_by)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                position_version_id,
                position_id,
                bid_id,
                topic_version["topic_version_id"],
                1,
                "Net 60 days",
                "Net 45 days from invoice",
                "ACCEPT",
                "Approved Bid position",
                "Jason",
                "Commercial",
                "Independent reviewer",
                "NOT_REQUIRED",
                "Customer terms",
                "Approved",
                provenance,
                NOW.isoformat(),
                "Jason",
            ),
        )
        conn.execute(
            "INSERT INTO scenario_families VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                scenario_family_id,
                bid_id,
                "BASE",
                "BASE",
                "Approved base Bid",
                "Jason",
                "Customer issue basis",
                "ACTIVE",
                "MATERIAL",
                None,
                1,
                "Jason",
                NOW.isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO scenario_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                scenario_version_id,
                scenario_family_id,
                bid_id,
                1,
                "REVIEW_ACCEPTED",
                "CAD",
                2,
                "ACTUAL_365",
                "[]",
                "[]",
                "[]",
                "[]",
                "Jason",
                NOW.isoformat(),
                "b" * 64,
            ),
        )
        conn.execute(
            "INSERT INTO scenario_baselines VALUES(?,?,?,?,?,?)",
            (
                f"SBL-{suffix}",
                bid_id,
                scenario_version_id,
                "Jason",
                NOW.isoformat(),
                "Approved base",
            ),
        )
        conn.execute(
            "INSERT INTO proposal_families VALUES(?,?,?,?,?,?,?,?)",
            (
                f"PFA-{suffix}",
                bid_id,
                "OFFER",
                "PROPOSAL_REQUIRED",
                "Customer Proposal",
                "Jason",
                "Jason",
                NOW.isoformat(),
            ),
        )
        conn.execute(
            """INSERT INTO deliverable_items(deliverable_id,bid_id,title,description,category,
            criticality,materiality,lifecycle_phase,direction,workflow_state,owner,recipient,
            due_basis,fixed_due_date,condition_active,version,provenance_json,created_at,
            updated_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"DEL-{suffix}",
                bid_id,
                "Equipment delivery",
                "Deliver equipment by the agreed date.",
                "SCHEDULE",
                "MANDATORY",
                "MATERIAL",
                "WITH_BID",
                "COMPANY_TO_CUSTOMER",
                "SATISFIED",
                "Jason",
                "Customer",
                "FIXED_DATE",
                "2027-01-15",
                1,
                1,
                provenance,
                NOW.isoformat(),
                NOW.isoformat(),
                "Jason",
            ),
        )
        conn.commit()


def generation_manifest(
    package_id: str,
    package_hash: str,
    docx: bytes,
    pdf: bytes | None = None,
    *,
    revision: str = "A",
) -> dict[str, Any]:
    return {
        "schema_id": "urn:proposal-studio:contract:proposal-generation-manifest:v1",
        "schema_version": "1.0.0",
        "proposal_id": "PROP-OPS09",
        "proposal_revision": revision,
        "source_proposal_package": {"package_id": package_id, "sha256": package_hash},
        "template": {"template_id": "TPL-MAJOR", "template_version": 1},
        "generation_application": {"name": "Proposal Studio", "version": "1.0.0"},
        "generated_at": "2026-09-10T12:30:00Z",
        "generated_files": {
            "docx": {
                "filename": f"proposal-{revision}.docx",
                "sha256": hashlib.sha256(docx).hexdigest(),
            },
            "pdf": (
                {
                    "filename": f"proposal-{revision}.pdf",
                    "sha256": hashlib.sha256(pdf).hexdigest(),
                }
                if pdf is not None
                else None
            ),
        },
        "included_sections": [{"order": 10, "section_key": "cover", "title": "Cover"}],
        "excluded_sections": [],
        "manual_narratives": [],
        "manual_overrides": [],
        "warnings": [],
        "unresolved_placeholders": [],
        "generation_status": "SUCCEEDED",
    }


def import_candidate(
    service: ProposalExchangeService,
    export_id: str,
    package_id: str,
    package_hash: str,
    revision: str = "A",
) -> tuple[bytes, bytes, str]:
    docx = f"controlled docx {revision}".encode()
    pdf = f"%PDF controlled {revision}".encode()
    manifest = generation_manifest(package_id, package_hash, docx, pdf, revision=revision)
    candidate = service.import_manifest(
        BID_ID,
        export_id,
        json.dumps(manifest).encode(),
        {
            "docx": (f"proposal-{revision}.docx", docx),
            "pdf": (f"proposal-{revision}.pdf", pdf),
        },
        "Jason",
    )
    return docx, pdf, candidate.candidate_id


def test_frozen_contract_examples_and_canonical_hash() -> None:
    package = strict_json_loads(
        (CONTRACT_ROOT / "examples" / "proposal-package-v1.example.json").read_bytes()
    )
    manifest = strict_json_loads(
        (CONTRACT_ROOT / "examples" / "proposal-generation-manifest-v1.example.json").read_bytes()
    )
    validate_package(package)
    validate_manifest(manifest)
    assert canonical_sha256(package) == (
        "f087af73dc3306d0ca174ffaa6df464f5c6102a86929926a038797e9b71b310b"
    )
    assert manifest["source_proposal_package"] == {
        "package_id": package["package_id"],
        "sha256": canonical_sha256(package),
    }


def test_contract_rejects_duplicate_keys_order_unknown_and_unsafe_paths() -> None:
    with pytest.raises(ExchangeContractError, match="Duplicate"):
        strict_json_loads(b'{"schema_id":"one","schema_id":"two"}')
    package = strict_json_loads(
        (CONTRACT_ROOT / "examples" / "proposal-package-v1.example.json").read_bytes()
    )
    invalid_order = copy.deepcopy(package)
    invalid_order["requirements"] = list(reversed(invalid_order["requirements"]))
    with pytest.raises(ExchangeContractError, match="order"):
        validate_package(invalid_order)
    unknown = copy.deepcopy(package)
    unknown["unexpected"] = True
    with pytest.raises(ExchangeContractError, match="unknown"):
        validate_package(unknown)
    for bad_path in ("/etc/passwd", "../secret.pdf", "documents/../secret.pdf", "a//b.pdf"):
        unsafe = copy.deepcopy(package)
        unsafe["supporting_documents"][0]["relative_path"] = bad_path
        with pytest.raises(ExchangeContractError):
            validate_package(unsafe)


def test_export_replay_and_changed_source_staleness(tmp_path: Path) -> None:
    service, bid_repository = service_with_ready_bid(tmp_path)
    assert service.assess(BID_ID).status is ProposalControlStatus.READY_FOR_EXPORT
    first, replayed = service.export_package(BID_ID, "Jason")
    second, replayed_second = service.export_package(BID_ID, "Jason")
    assert not replayed and replayed_second and first.export_id == second.export_id
    bid = bid_repository.get_bid(BID_ID)
    assert bid is not None
    bid_repository.update_bid(bid.model_copy(update={"project_name": "Changed North Plant Bid"}))
    assessment = service.assess(BID_ID)
    assert assessment.status is ProposalControlStatus.STALE
    assert "bid_identity" in assessment.changed_areas
    successor, replayed_successor = service.export_package(BID_ID, "Jason")
    assert not replayed_successor
    assert successor.supersedes_export_id == first.export_id


def test_manifest_artifact_validation_and_cross_bid_protection(tmp_path: Path) -> None:
    service, bid_repository = service_with_ready_bid(tmp_path)
    export, _ = service.export_package(BID_ID, "Jason")
    docx = b"controlled docx"
    manifest = generation_manifest(export.package_id, export.canonical_sha256, docx)
    wrong_version = copy.deepcopy(manifest)
    wrong_version["schema_version"] = "1.0.1"
    with pytest.raises(ProposalImportError, match="schema_version|version"):
        service.import_manifest(
            BID_ID,
            export.export_id,
            json.dumps(wrong_version).encode(),
            {"docx": ("proposal-A.docx", docx)},
            "Jason",
        )
    with pytest.raises(ProposalImportError, match="missing"):
        service.import_manifest(
            BID_ID,
            export.export_id,
            json.dumps(manifest).encode(),
            {},
            "Jason",
        )
    mismatch = copy.deepcopy(manifest)
    mismatch["source_proposal_package"]["sha256"] = "0" * 64
    with pytest.raises(ProposalImportError, match="hash"):
        service.import_manifest(
            BID_ID,
            export.export_id,
            json.dumps(mismatch).encode(),
            {"docx": ("proposal-A.docx", docx)},
            "Jason",
        )
    with pytest.raises(ProposalImportError, match="SHA-256"):
        service.import_manifest(
            BID_ID,
            export.export_id,
            json.dumps(manifest).encode(),
            {"docx": ("proposal-A.docx", b"tampered")},
            "Jason",
        )
    traversal = copy.deepcopy(manifest)
    traversal["generated_files"]["docx"]["filename"] = "../proposal-A.docx"
    with pytest.raises(ProposalImportError):
        service.import_manifest(
            BID_ID,
            export.export_id,
            json.dumps(traversal).encode(),
            {"docx": ("../proposal-A.docx", docx)},
            "Jason",
        )
    duplicate_destination = generation_manifest(
        export.package_id,
        export.canonical_sha256,
        docx,
        docx,
    )
    duplicate_destination["generated_files"]["pdf"]["filename"] = "proposal-A.docx"
    with pytest.raises(ProposalImportError, match="allowed shape|Duplicate normalized"):
        service.import_manifest(
            BID_ID,
            export.export_id,
            json.dumps(duplicate_destination).encode(),
            {
                "docx": ("proposal-A.docx", docx),
                "pdf": ("proposal-A.docx", docx),
            },
            "Jason",
        )
    second_bid = "B-2026-0910"
    seed_ready_bid(service.db, bid_repository, second_bid)
    with pytest.raises(ProposalExchangeNotFoundError):
        service.export_by_id(second_bid, export.export_id)
    candidate = service.import_manifest(
        BID_ID,
        export.export_id,
        json.dumps(manifest).encode(),
        {"docx": ("proposal-A.docx", docx)},
        "Jason",
    )
    assert candidate.status is ProposalControlStatus.GENERATED_ARTIFACTS_RECEIVED


def test_approval_issue_immutability_successor_and_handover(tmp_path: Path) -> None:
    service, bid_repository = service_with_ready_bid(tmp_path)
    export, _ = service.export_package(BID_ID, "Jason")
    _, _, candidate_id = import_candidate(
        service, export.export_id, export.package_id, export.canonical_sha256
    )
    candidate = service.history(BID_ID).candidates[0]
    with pytest.raises(StaleProposalCandidateError):
        service.approve_candidate(BID_ID, candidate_id, candidate.version + 1, "Jason")
    approved = service.approve_candidate(BID_ID, candidate_id, candidate.version, "Jason")
    baseline = service.issue(
        BID_ID,
        CustomerIssueCommand(
            candidate_id=candidate_id,
            expected_version=approved.version,
            issue_revision="A",
            issued_at=NOW,
            issue_method="Customer portal",
            destination_reference="Portal receipt 123",
            offer_valid_until=date(2026, 10, 10),
            note="Manually uploaded by Jason",
        ),
        "Jason",
    )
    assert service.assess(BID_ID).status is ProposalControlStatus.ISSUED
    rows = service.handover_rows(BID_ID)
    assert rows[0]["issue_revision"] == "A"
    assert export.canonical_sha256 in str(rows[0]["package_sha256"])
    with service.db._conn() as conn:
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute(
                "UPDATE issued_offer_baselines SET note='changed' WHERE baseline_id=?",
                (baseline.baseline_id,),
            )
        with pytest.raises(sqlite3.DatabaseError, match="cannot be deleted"):
            conn.execute(
                "DELETE FROM issued_offer_baselines WHERE baseline_id=?", (baseline.baseline_id,)
            )
    bid = bid_repository.get_bid(BID_ID)
    assert bid is not None
    bid_repository.update_bid(bid.model_copy(update={"project_name": "North Plant Rev B"}))
    successor_export, _ = service.export_package(BID_ID, "Jason")
    assert service.assess(BID_ID).status is ProposalControlStatus.SUPERSEDED
    assert service.history(BID_ID).candidates[0].status is ProposalControlStatus.ISSUED
    _, _, successor_candidate_id = import_candidate(
        service,
        successor_export.export_id,
        successor_export.package_id,
        successor_export.canonical_sha256,
        "B",
    )
    successor = service.history(BID_ID).candidates[0]
    assert successor.candidate_id == successor_candidate_id
    assert successor.supersedes_candidate_id == candidate_id
    assert service.history(BID_ID).candidates[1].status is ProposalControlStatus.SUPERSEDED


def test_required_approval_enforcement_and_audit_failure_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = service_with_ready_bid(tmp_path)
    provenance = Provenance.from_human("Jason")
    approved_record = Approval(
        approval_id="APR-OPS09",
        bid_id=BID_ID,
        approval_type="legal",
        required=True,
        obtained=True,
        authority="Legal Director",
        evidence_ref="Decision record 9",
        decision="APPROVED",
        decided_at=NOW,
        provenance=provenance,
    )
    service.bid_repository.create_approval(approved_record)
    export, _ = service.export_package(BID_ID, "Jason")
    _, _, candidate_id = import_candidate(
        service, export.export_id, export.package_id, export.canonical_sha256
    )
    service.bid_repository.update_approval(
        Approval(
            approval_id="APR-OPS09",
            bid_id=BID_ID,
            approval_type="legal",
            required=True,
            obtained=False,
            authority="Legal Director",
            evidence_ref="Decision record 9",
            decision="APPROVED",
            decided_at=NOW,
            provenance=provenance,
        )
    )
    candidate = service.history(BID_ID).candidates[0]
    with pytest.raises(ValueError, match="blocked"):
        service.approve_candidate(BID_ID, candidate_id, candidate.version, "Jason")
    service.bid_repository.update_approval(approved_record)
    approved = service.approve_candidate(BID_ID, candidate_id, candidate.version, "Jason")

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("injected audit failure")

    monkeypatch.setattr(service, "_audit", fail_audit)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        service.issue(
            BID_ID,
            CustomerIssueCommand(
                candidate_id=candidate_id,
                expected_version=approved.version,
                issue_revision="A",
                issued_at=NOW,
                issue_method="Customer portal",
                destination_reference="Portal receipt 123",
            ),
            "Jason",
        )
    with service.db._conn() as conn:
        assert (
            conn.execute("SELECT count(*) FROM proposal_customer_issue_events").fetchone()[0] == 0
        )
        assert conn.execute("SELECT count(*) FROM issued_offer_baselines").fetchone()[0] == 0
    assert service.history(BID_ID).candidates[0].status is ProposalControlStatus.APPROVED_FOR_ISSUE


def test_export_audit_failure_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = service_with_ready_bid(tmp_path)

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("injected audit failure")

    monkeypatch.setattr(service, "_audit", fail_audit)
    with pytest.raises(sqlite3.OperationalError):
        service.export_package(BID_ID, "Jason")
    assert service.history(BID_ID).exports == ()


def test_get_style_queries_do_not_mutate(tmp_path: Path) -> None:
    service, _ = service_with_ready_bid(tmp_path)
    with service.db._conn() as conn:
        before = tuple(
            conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "audit_log",
                "proposal_exchange_exports",
                "proposal_issue_candidates",
                "issued_offer_baselines",
            )
        )
    service.assess(BID_ID)
    service.history(BID_ID)
    service.handover_rows(BID_ID)
    with service.db._conn() as conn:
        after = tuple(
            conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "audit_log",
                "proposal_exchange_exports",
                "proposal_issue_candidates",
                "issued_offer_baselines",
            )
        )
    assert after == before


def test_rendered_bid_context_workflow() -> None:
    sys.path.insert(0, str(Path(__file__).parents[2]))
    from scripts.asgi_acceptance_ops09 import main

    measurements = asyncio.run(main())
    assert measurements["user_actions"] >= 5
    assert all(value == 0 for key, value in measurements.items() if key != "user_actions")
