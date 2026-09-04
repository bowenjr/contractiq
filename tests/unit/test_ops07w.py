"""Focused OPS-07W classification, exact-evidence, work, and migration regressions."""

from datetime import date
from decimal import Decimal

import pytest

from core.approval_repository import ApprovalRepository
from core.bid_repository import BidRepository
from core.commercial_repository import CommercialRepository
from core.contract_risk_repository import ContractRiskRepository
from core.database import Database
from core.deliverable_repository import DeliverableRepository
from core.document_repository import DocumentRepository
from core.enums import ApprovalType, BidLevel, RiskTrigger
from core.ops07 import Ops07Repository
from core.ops07w import ClassificationAssessmentCommand, Ops07WorkflowRepository, build_navigator
from core.proposal_repository import ProposalRepository
from core.requirement_repository import RequirementRepository
from core.requirement_service import RequirementService
from core.schemas import Approval, Provenance
from core.scope_repository import ScopeInterfaceRepository
from core.vendor_document_control import (
    CustomerRequirementCreate,
    RequirementVerificationUpdate,
    SupplierPackageCreate,
    VerificationStatus,
)
from core.vendor_document_repository import VendorDocumentRepository
from core.vendor_document_service import VendorDocumentService
from core.work_item_repository import WorkItemRepository
from core.work_items import WorkCategory


def _stack(tmp_path, bid):
    db = Database(tmp_path / "ops07w.db")
    bids = BidRepository(db)
    bids.create_bid(bid)
    WorkItemRepository(db)
    documents = DocumentRepository(db)
    requirements = RequirementRepository(db)
    ScopeInterfaceRepository(db)
    vendors = VendorDocumentRepository(db)
    CommercialRepository(db)
    ContractRiskRepository(db)
    ApprovalRepository(db)
    ProposalRepository(db)
    DeliverableRepository(db)
    ops07 = Ops07Repository(db)
    workflow = Ops07WorkflowRepository(db)
    return (
        db,
        bids,
        RequirementService(requirements, bids, documents),
        VendorDocumentService(vendors, bids),
        ops07,
        workflow,
    )


def _command(**changes):
    values = {
        "estimated_value": Decimal("1000000"),
        "customer_type": "EPCM",
        "is_epc_epcm": True,
        "selected_level": BidLevel.LEVEL_3,
    }
    values.update(changes)
    return ClassificationAssessmentCommand.model_validate(values)


def test_epcm_floor_override_policy_and_append_only_evidence(tmp_path, valid_bid):
    bid = valid_bid.model_copy(update={"classification": BidLevel.LEVEL_3})
    db, _bids, _requirements, _vendors, _ops07, workflow = _stack(tmp_path, bid)
    result = workflow.assess(_command())
    assert result.level is BidLevel.LEVEL_3
    with pytest.raises(ValueError, match="cannot be lower"):
        workflow.record_assessment(bid.bid_id, _command(selected_level=BidLevel.LEVEL_2), "Jason")
    with pytest.raises(ValueError, match="reason is required"):
        workflow.record_assessment(bid.bid_id, _command(selected_level=BidLevel.LEVEL_4), "Jason")
    first = workflow.record_assessment(
        bid.bid_id,
        _command(selected_level=BidLevel.LEVEL_4, override_rationale="Strategic review"),
        "Jason",
    )
    assert first.sequence == 1 and first.override_rationale == "Strategic review"
    assert workflow.latest_assessment(bid.bid_id) == first
    with db._conn() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM audit_log WHERE action='bid_classification_assessed'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("trigger", list(RiskTrigger))
def test_every_authoritative_trigger_floor_is_honoured(trigger: RiskTrigger) -> None:
    command = _command(
        estimated_value=Decimal("0"),
        customer_type="DIRECT",
        is_epc_epcm=False,
        triggers=[trigger],
        selected_level=BidLevel.LEVEL_4,
        override_rationale="Test permitted upward selection",
    )
    result = Ops07WorkflowRepository.assess(command)
    assert result.level.value >= result.trigger_floor_level.value


def test_exact_evidence_is_requirement_specific_and_all_rows_must_clear(tmp_path, valid_bid):
    db, _bids, requirements, vendors, ops07, workflow = _stack(tmp_path, valid_bid)
    requirement = requirements.create_requirement(
        {
            "bid_id": valid_bid.bid_id,
            "title": "Arc flash",
            "statement": "Manufacturer shall confirm",
            "origin": "INTERNAL",
            "category": "TECHNICAL",
            "significance": "MANDATORY",
        },
        "Jason",
    )
    vendors.ensure_standard_template()
    package = vendors.create_package(
        SupplierPackageCreate(
            bid_id=valid_bid.bid_id,
            package_name="LV MCC",
            package_code="MCC",
            proposed_manufacturer="Example Manufacturer",
            internal_owner="Jason",
        ),
        "Jason",
    )
    first = vendors.create_requirement(
        CustomerRequirementCreate(
            package_id=package.package_id,
            customer_requirement_code="ARC",
            deliverable_title="Arc-flash letter",
        ),
        "Jason",
    )
    unrelated = vendors.create_requirement(
        CustomerRequirementCreate(
            package_id=package.package_id,
            customer_requirement_code="DRAW",
            deliverable_title="General arrangement",
        ),
        "Jason",
    )
    ops07.link_manufacturer(
        valid_bid.bid_id, requirement.requirement_id, package.package_id, "Jason"
    )
    workflow.link_exact_evidence(
        valid_bid.bid_id, requirement.requirement_id, first.requirement_id, "Jason"
    )
    assert [
        row.verification_row_id for row in workflow.exact_evidence(requirement.requirement_id)
    ] == [first.requirement_id]
    assert unrelated.requirement_id not in ops07.handover_csv(valid_bid.bid_id)
    assert not workflow.exact_evidence_clear(requirement.requirement_id)
    vendors.update_verification(
        first.requirement_id,
        RequirementVerificationUpdate(
            expected_version=1,
            verification_status=VerificationStatus.CONFIRMED_COMPLIANT,
            proposed_manufacturer="Example Manufacturer",
            response_source="Manufacturer compliance letter",
            response_received_date=date(2026, 8, 31),
            commercial_impact="NONE",
            bid_disposition="NONE",
        ),
        "Jason",
    )
    assert workflow.exact_evidence_clear(requirement.requirement_id)
    workflow.link_exact_evidence(
        valid_bid.bid_id, requirement.requirement_id, unrelated.requirement_id, "Jason"
    )
    assert not workflow.exact_evidence_clear(requirement.requirement_id)
    assert "Arc-flash letter" in ops07.handover_csv(valid_bid.bid_id)
    assert "General arrangement" in ops07.handover_csv(valid_bid.bid_id)
    workflow.unlink_exact_evidence(
        valid_bid.bid_id, requirement.requirement_id, unrelated.requirement_id, "Jason"
    )
    with db._conn() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    Ops07WorkflowRepository(Database(tmp_path / "ops07w.db"))


def test_contextual_work_creation_and_link_are_atomic(tmp_path, valid_bid):
    db, _bids, requirements, _vendors, _ops07, workflow = _stack(tmp_path, valid_bid)
    requirement = requirements.create_requirement(
        {
            "bid_id": valid_bid.bid_id,
            "title": "Follow-up",
            "statement": "Obtain letter",
            "origin": "INTERNAL",
            "category": "TECHNICAL",
            "significance": "MANDATORY",
        },
        "Jason",
    )
    item = workflow.create_linked_work(
        source_kind="requirement",
        source_id=requirement.requirement_id,
        title="Obtain letter",
        purpose="manufacturer evidence",
        category=WorkCategory.PRODUCT_TECHNICAL,
        actor="Jason",
    )
    assert (
        workflow.linked_work("requirement", requirement.requirement_id)[0]["work_item_id"]
        == item.work_item_id
    )
    before = db._conn().execute("SELECT count(*) FROM work_items").fetchone()[0]
    with pytest.raises(ValueError):
        workflow.create_linked_work(
            source_kind="requirement",
            source_id="REQ-MISSING",
            title="Must roll back",
            purpose="manufacturer evidence",
            category=WorkCategory.PRODUCT_TECHNICAL,
            actor="Jason",
        )
    assert db._conn().execute("SELECT count(*) FROM work_items").fetchone()[0] == before


def test_exact_evidence_rejects_duplicate_cross_bid_and_unassociated_rows(tmp_path, valid_bid):
    _db, bids, requirements, vendors, ops07, workflow = _stack(tmp_path, valid_bid)
    other = valid_bid.model_copy(update={"bid_id": "B-2026-9999"})
    bids.create_bid(other)
    requirement = requirements.create_requirement(
        {
            "bid_id": valid_bid.bid_id,
            "title": "Coverage",
            "statement": "Coverage",
            "origin": "INTERNAL",
            "category": "TECHNICAL",
            "significance": "MANDATORY",
        },
        "Jason",
    )
    vendors.ensure_standard_template()
    package = vendors.create_package(
        SupplierPackageCreate(
            bid_id=valid_bid.bid_id,
            package_name="Local",
            package_code="LOCAL",
            proposed_manufacturer="Maker",
            internal_owner="Jason",
        ),
        "Jason",
    )
    row = vendors.create_requirement(
        CustomerRequirementCreate(
            package_id=package.package_id,
            customer_requirement_code="LOCAL-1",
            deliverable_title="Letter",
        ),
        "Jason",
    )
    with pytest.raises(ValueError, match="Associate"):
        workflow.link_exact_evidence(
            valid_bid.bid_id, requirement.requirement_id, row.requirement_id, "Jason"
        )
    ops07.link_manufacturer(
        valid_bid.bid_id, requirement.requirement_id, package.package_id, "Jason"
    )
    workflow.link_exact_evidence(
        valid_bid.bid_id, requirement.requirement_id, row.requirement_id, "Jason"
    )
    with pytest.raises(ValueError, match="already linked"):
        workflow.link_exact_evidence(
            valid_bid.bid_id, requirement.requirement_id, row.requirement_id, "Jason"
        )
    foreign = vendors.create_package(
        SupplierPackageCreate(
            bid_id=other.bid_id,
            package_name="Foreign",
            package_code="FOREIGN",
            proposed_manufacturer="Maker",
            internal_owner="Jason",
        ),
        "Jason",
    )
    foreign_row = vendors.create_requirement(
        CustomerRequirementCreate(
            package_id=foreign.package_id,
            customer_requirement_code="FOREIGN-1",
            deliverable_title="Foreign letter",
        ),
        "Jason",
    )
    with pytest.raises(ValueError, match="same Bid"):
        workflow.link_exact_evidence(
            valid_bid.bid_id, requirement.requirement_id, foreign_row.requirement_id, "Jason"
        )


def test_navigator_query_count_is_independent_of_unrelated_bid_volume(tmp_path, valid_bid):
    db, bids, _requirements, _vendors, _ops07, workflow = _stack(tmp_path, valid_bid)
    for number in range(30):
        bids.create_bid(valid_bid.model_copy(update={"bid_id": f"B-2027-{number:04d}"}))
    statements: list[str] = []
    connection = db._conn()
    connection.set_trace_callback(statements.append)
    workflow._conn = lambda: connection  # type: ignore[method-assign]
    counts = workflow.navigator_counts(valid_bid.bid_id)
    reads = [
        statement for statement in statements if statement.lstrip().upper().startswith("SELECT")
    ]
    assert counts["requirements"] == 0
    assert len(reads) == 3


@pytest.mark.parametrize(
    ("changes", "stage", "expected"),
    [
        ({}, "manufacturer", "Not started"),
        ({"packages": 1}, "manufacturer", "In progress"),
        (
            {
                "packages": 1,
                "manufacturer_associations": 1,
                "manufacturer_requirements": 1,
                "manufacturer_complete": 1,
            },
            "manufacturer",
            "Complete",
        ),
        (
            {
                "packages": 1,
                "manufacturer_associations": 1,
                "manufacturer_requirements": 1,
                "manufacturer_blocked": 1,
            },
            "manufacturer",
            "Blocked",
        ),
        ({"commercial": 1}, "commercial", "In progress"),
        (
            {"commercial": 1, "risks": 1, "decisions": 1, "approvals": 1},
            "commercial",
            "Complete",
        ),
    ],
)
def test_navigator_truthfully_projects_empty_partial_complete_and_blocked(
    changes: dict[str, int], stage: str, expected: str
) -> None:
    counts = {
        "documents": 0,
        "requirements": 0,
        "responses": 0,
        "scopes": 0,
        "interfaces": 0,
        "packages": 0,
        "manufacturer_associations": 0,
        "manufacturer_requirements": 0,
        "manufacturer_complete": 0,
        "manufacturer_blocked": 0,
        "commercial": 0,
        "risks": 0,
        "decisions": 0,
        "approvals": 0,
        "proposals": 0,
        "deliverables": 0,
        "assessments": 0,
    }
    counts.update(changes)
    projected = {item.key: item for item in build_navigator("B-1", counts, "hold", "Jason")}
    assert projected[stage].status == expected


def test_gate_approval_and_audit_are_atomic_under_audit_failure(tmp_path, valid_bid) -> None:
    db = Database(tmp_path / "approval-audit.db")
    bids = BidRepository(db)
    bids.create_bid(valid_bid)
    approval = Approval(
        approval_id="APR-ATOMIC",
        bid_id=valid_bid.bid_id,
        approval_type=ApprovalType.BID_NO_BID,
        obtained=True,
        authority="Director",
        evidence_ref="Minute",
        decision="Proceed",
        provenance=Provenance.from_human("Jason"),
    )
    with db._conn() as conn:
        conn.execute(
            """CREATE TRIGGER reject_approval_audit BEFORE INSERT ON audit_log
            WHEN NEW.action='bid_gate_approval_created'
            BEGIN SELECT RAISE(ABORT,'injected audit failure'); END"""
        )
    with pytest.raises(Exception, match="injected audit failure"):
        bids.create_approval(approval, "Jason")
    assert bids.list_approvals(valid_bid.bid_id) == []
    assert not [
        entry
        for entry in bids.list_audit(valid_bid.bid_id)
        if entry.action == "bid_gate_approval_created"
    ]
