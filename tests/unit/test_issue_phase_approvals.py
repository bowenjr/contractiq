from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core.bid_repository import BidRepository
from core.database import Database
from core.enums import BidLevel, BidStatus
from core.handover import BidHandoverService
from core.owner_decision_service import OwnerDecisionService
from core.proposal_exchange import CustomerIssueCommand, ProposalControlStatus
from core.readiness_phases import BlockerPhase
from core.readiness_service import evaluate_readiness, request_override
from core.schemas import Approval, Provenance
from core.senior_approvals import derive_required_approvals
from core.work_item_repository import WorkItemRepository
from tests.unit.test_ops09 import (
    BID_ID,
    NOW,
    add_effective_policy,
    import_candidate,
    service_with_ready_bid,
)


def _level(service: object, repository: object, level: BidLevel) -> None:
    bid = repository.get_bid(BID_ID)  # type: ignore[attr-defined]
    assert bid is not None
    repository.update_bid(bid.model_copy(update={"classification": level}))  # type: ignore[attr-defined]


def _pursue(service: object) -> str:
    decision = OwnerDecisionService(service.db).record_owner_decision(  # type: ignore[attr-defined]
        BID_ID,
        "PURSUE",
        "The owner approved continued pursuit for this issue-control test.",
        "Jason",
    )
    return decision.decision_id


def _approval(service: object, approval_id: str, approval_type: str, obtained: bool) -> None:
    with service.db._conn() as conn:  # type: ignore[attr-defined]
        baseline = conn.execute(
            "SELECT selection_id FROM scenario_baselines WHERE bid_id=? "
            "ORDER BY selected_at DESC,selection_id DESC LIMIT 1",
            (BID_ID,),
        ).fetchone()
    service.bid_repository.create_approval(  # type: ignore[attr-defined]
        Approval(
            approval_id=approval_id,
            bid_id=BID_ID,
            approval_type=approval_type,
            required=True,
            obtained=obtained,
            authority="MARGIN" if obtained else None,
            evidence_ref="Controlled approval evidence" if obtained else None,
            decision="APPROVED" if obtained else "PENDING",
            decided_at=NOW if obtained else None,
            provenance=Provenance.from_human("Jason"),
            pricing_baseline_id=(
                str(baseline["selection_id"]) if obtained and baseline is not None else None
            ),
        )
    )


def _command(candidate_id: str, version: int) -> CustomerIssueCommand:
    return CustomerIssueCommand(
        candidate_id=candidate_id,
        expected_version=version,
        issue_revision="A",
        issued_at=NOW,
        issue_method="Customer portal",
        destination_reference="Receipt F-B",
        offer_valid_until=date(2026, 10, 10),
    )


def test_g4_level_zero_without_requirement_can_export_approve_and_issue(
    tmp_path: Path,
) -> None:
    service, _ = service_with_ready_bid(tmp_path)
    assessment = service.assess(BID_ID)
    assert assessment.approvals_clear
    export, _ = service.export_package(BID_ID, "Jason")
    assert not export.is_draft
    _, _, candidate_id = import_candidate(
        service, export.export_id, export.package_id, export.canonical_sha256
    )
    candidate = service.history(BID_ID).candidates[0]
    approved = service.approve_candidate(BID_ID, candidate_id, candidate.version, "Jason")
    issued = service.issue(BID_ID, _command(candidate_id, approved.version), "Jason")
    assert issued.issue_revision == "A"


def test_g8_g10_g17_g18_required_without_policy_exports_marked_draft(
    tmp_path: Path,
) -> None:
    service, repository = service_with_ready_bid(tmp_path)
    _level(service, repository, BidLevel.LEVEL_3)
    _pursue(service)

    readiness = evaluate_readiness(repository, service.db, BID_ID)
    assert readiness.verdict.value == "clear"
    assert readiness.blockers == []
    assert {item.condition_id for item in readiness.issue_blockers} >= {
        "g4.margin_approved",
        "g6.senior_approvals_current",
    }
    assessment = service.assess(BID_ID)
    assert not assessment.approvals_clear
    assert assessment.pending_before_issue
    assert all(
        blocker.phase is BlockerPhase.ISSUE
        for blocker in assessment.blockers
        if blocker.code.startswith("GATE_G4") or blocker.code.startswith("GATE_G6")
    )

    export, _ = service.export_package(BID_ID, "Jason")
    assert export.is_draft
    assert export.pending_before_issue
    assert export.suggested_filename.startswith("DRAFT-")
    _, _, candidate_id = import_candidate(
        service, export.export_id, export.package_id, export.canonical_sha256
    )
    candidate = service.history(BID_ID).candidates[0]
    with pytest.raises(ValueError, match="blocked"):
        service.approve_candidate(BID_ID, candidate_id, candidate.version, "Jason")
    with pytest.raises(ValueError):
        service.issue(BID_ID, _command(candidate_id, candidate.version), "Jason")

    add_effective_policy(service)
    pending_export, _ = service.export_package(BID_ID, "Jason")
    assert pending_export.is_draft
    assert not any(
        "authority policy not published" in item for item in pending_export.pending_before_issue
    )
    assert any(
        "Missing current approval evidence" in item for item in pending_export.pending_before_issue
    )

    WorkItemRepository(service.db)
    handover = BidHandoverService(service.db, proposal_readiness_loader=service.assess).report(
        BID_ID, gate_verdict="clear", gate_blockers=[], generated_by="test"
    )
    assert not handover.handover_ready


def test_g9_missing_pricing_basis_allows_valid_draft_projection(tmp_path: Path) -> None:
    service, repository = service_with_ready_bid(tmp_path)
    _level(service, repository, BidLevel.LEVEL_2)
    _pursue(service)
    _approval(service, "APR-MARGIN", "margin", True)
    add_effective_policy(service)
    with service.db._conn() as conn:
        conn.execute("DELETE FROM scenario_baselines WHERE bid_id=?", (BID_ID,))
        conn.commit()

    export, _ = service.export_package(BID_ID, "Jason")
    assert export.is_draft
    assert any("Pricing basis missing" in item for item in export.pending_before_issue)
    assert '"selected_pricing_scenario":null' in export.canonical_package_json


def test_g11_current_policy_pricing_and_margin_clear_full_chain(tmp_path: Path) -> None:
    service, repository = service_with_ready_bid(tmp_path)
    _level(service, repository, BidLevel.LEVEL_3)
    _pursue(service)
    _approval(service, "APR-MARGIN", "margin", True)
    add_effective_policy(service)

    export, _ = service.export_package(BID_ID, "Jason")
    assert not export.is_draft
    assert service.assess(BID_ID).approvals_clear
    _, _, candidate_id = import_candidate(
        service, export.export_id, export.package_id, export.canonical_sha256
    )
    candidate = service.history(BID_ID).candidates[0]
    approved = service.approve_candidate(BID_ID, candidate_id, candidate.version, "Jason")
    service.issue(BID_ID, _command(candidate_id, approved.version), "Jason")
    assert service.assess(BID_ID).status is ProposalControlStatus.ISSUED


def test_legacy_obtained_approval_without_baseline_cannot_satisfy_issue_phase(
    tmp_path: Path,
) -> None:
    """The additive migration preserves legacy evidence but never upgrades its authority."""
    service, repository = service_with_ready_bid(tmp_path)
    _level(service, repository, BidLevel.LEVEL_2)
    _pursue(service)
    add_effective_policy(service)
    provenance = Provenance.from_human("Legacy operator").model_dump_json()
    with service.db._conn() as conn:
        conn.execute("DROP TABLE approvals")
        conn.execute(
            """CREATE TABLE approvals(
            approval_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,approval_type TEXT NOT NULL,
            required INTEGER NOT NULL DEFAULT 1,obtained INTEGER NOT NULL DEFAULT 0,
            authority TEXT,evidence_ref TEXT,decision TEXT,decided_at TEXT,
            provenance_json TEXT NOT NULL)"""
        )
        conn.execute(
            "INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "APR-LEGACY-NO-BASELINE",
                BID_ID,
                "margin",
                1,
                1,
                "margin",
                "Legacy email record",
                "APPROVED",
                "2025-01-01T00:00:00+00:00",
                provenance,
            ),
        )
        conn.commit()

    migrated = BidRepository(Database(Path(service.db.db_path)))
    service.bid_repository = migrated
    assessment = service.assess(BID_ID)

    assert not assessment.approvals_clear
    assert any(
        "recorded before a pricing baseline was identified" in item
        for item in assessment.pending_before_issue
    )
    with service.db._conn() as conn:
        row = conn.execute(
            "SELECT approval_id,obtained,authority,evidence_ref,decision,pricing_baseline_id "
            "FROM approvals WHERE approval_id='APR-LEGACY-NO-BASELINE'"
        ).fetchone()
    assert tuple(row) == (
        "APR-LEGACY-NO-BASELINE",
        1,
        "margin",
        "Legacy email record",
        "APPROVED",
        None,
    )


def test_g5_g7_stopped_bid_refuses_export_reapprove_issue_then_reopens(
    tmp_path: Path,
) -> None:
    service, repository = service_with_ready_bid(tmp_path)
    current_id = _pursue(service)
    export, _ = service.export_package(BID_ID, "Jason")
    _, _, candidate_id = import_candidate(
        service, export.export_id, export.package_id, export.canonical_sha256
    )
    candidate = service.history(BID_ID).candidates[0]
    approved = service.approve_candidate(BID_ID, candidate_id, candidate.version, "Jason")
    stopped = OwnerDecisionService(service.db).record_owner_decision(
        BID_ID,
        "DO_NOT_PURSUE",
        "The owner stopped this Bid after reviewing the generated candidate.",
        "Jason",
        expected_current_decision_id=current_id,
    )
    assert repository.get_bid(BID_ID).status is BidStatus.NO_BID  # type: ignore[union-attr]
    assert any(item.code == "BID_STOPPED" for item in service.assess(BID_ID).blockers)
    with pytest.raises(ValueError, match="stopped by owner decision"):
        service.export_package(BID_ID, "Jason")
    with pytest.raises(ValueError, match="blocked"):
        service.approve_candidate(BID_ID, candidate_id, approved.version, "Jason")
    with pytest.raises(ValueError):
        service.issue(BID_ID, _command(candidate_id, approved.version), "Jason")

    OwnerDecisionService(service.db).record_owner_decision(
        BID_ID,
        "PURSUE",
        "The owner reopened the Bid after resolving the stop reason.",
        "Jason",
        expected_current_decision_id=stopped.decision_id,
    )
    reopened, _ = service.export_package(BID_ID, "Jason")
    assert reopened.bid_id == BID_ID


def test_g12_legacy_do_not_pursue_approval_never_replaces_owner_decision(
    tmp_path: Path,
) -> None:
    service, repository = service_with_ready_bid(tmp_path)
    _level(service, repository, BidLevel.LEVEL_3)
    _approval(service, "APR-NB", "bid_no_bid", True)
    _approval(service, "APR-M", "margin", True)
    assessment = service.assess(BID_ID)
    assert any("G1.OWNER_PURSUIT_DECIDED" in item.code for item in assessment.blockers)
    with pytest.raises(ValueError):
        service.export_package(BID_ID, "Jason")
    with pytest.raises(ValueError):
        service.approve_candidate(BID_ID, "PIC-MISSING", 1, "Jason")
    with pytest.raises(ValueError):
        service.issue(BID_ID, _command("PIC-MISSING", 1), "Jason")


def test_g14_and_old_s3_s4_issue_override_exploit_is_closed(tmp_path: Path) -> None:
    service, repository = service_with_ready_bid(tmp_path)
    _approval(service, "APR-CREDIT", "credit", False)
    export, _ = service.export_package(BID_ID, "Jason")
    assert export.is_draft
    with pytest.raises(ValueError, match="Issue-phase"):
        request_override(
            repository,
            service.db,
            BID_ID,
            "g4.required_approvals",
            "Jason",
            "Old S4 override attempt",
        )


def test_derived_requirements_include_terms_rows_and_open_routes() -> None:
    requirements = derive_required_approvals(
        BidLevel.LEVEL_1,
        [
            {"position_id": "CP-1", "disposition": "QUALIFY", "required_approver": None},
            {
                "position_id": "CP-2",
                "disposition": "ACCEPT",
                "required_approver": "Legal Director",
            },
        ],
        [{"approval_type": "finance", "required": True}],
        [{"route_id": "ROUTE-1", "state": "PENDING"}],
    )
    assert {item.key for item in requirements} == {
        "commercial-role:CP-2:legal director",
        "approval-type:finance",
        "approval-route:ROUTE-1",
    }


def test_issue_phase_only_blockers_keep_draft_export_offered_in_the_control_status(
    tmp_path: Path,
) -> None:
    """The control status must not read BLOCKED for a draft export the service allows."""
    service, repository = service_with_ready_bid(tmp_path)
    _level(service, repository, BidLevel.LEVEL_3)
    _pursue(service)

    before = service.assess(BID_ID)
    assert before.status is ProposalControlStatus.READY_FOR_EXPORT
    assert before.next_action == "Prepare a draft Proposal Studio package"
    assert before.pending_before_issue

    export, _ = service.export_package(BID_ID, "Jason")
    assert export.is_draft
    exported = service.assess(BID_ID)
    assert exported.status is ProposalControlStatus.EXPORTED

    _, _, candidate_id = import_candidate(
        service, export.export_id, export.package_id, export.canonical_sha256
    )
    candidate = service.history(BID_ID).candidates[0]
    awaiting = service.assess(BID_ID)
    assert awaiting.status is ProposalControlStatus.BLOCKED
    assert awaiting.next_action == "Obtain the pending senior approvals before approving for issue"
    with pytest.raises(ValueError, match="blocked"):
        service.approve_candidate(BID_ID, candidate_id, candidate.version, "Jason")


def test_development_blockers_still_block_the_control_status_before_export(
    tmp_path: Path,
) -> None:
    service, repository = service_with_ready_bid(tmp_path)
    _level(service, repository, BidLevel.LEVEL_3)  # no owner decision recorded

    status = service.assess(BID_ID)
    assert status.status is ProposalControlStatus.BLOCKED
    assert status.next_action == "Record owner decision"
    assert status.next_action_path.startswith(f"/bids/{BID_ID}/owner-decision?return=")
    with pytest.raises(ValueError):
        service.export_package(BID_ID, "Jason")
