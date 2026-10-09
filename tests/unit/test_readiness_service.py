from decimal import Decimal

import pytest

from core.bid_repository import BidRepository
from core.database import Database
from core.enums import ApprovalType, BidLevel, Gate, GateStatus
from core.owner_decision_service import OwnerDecisionService
from core.readiness import ReadinessVerdict
from core.readiness_service import evaluate_readiness, request_override
from core.schemas import Approval, Bid, Provenance


def _approval(
    bid: Bid,
    provenance: Provenance,
    approval_type: ApprovalType,
    *,
    obtained: bool,
) -> Approval:
    return Approval(
        approval_id=f"APP-{approval_type.value}",
        bid_id=bid.bid_id,
        approval_type=approval_type,
        required=True,
        obtained=obtained,
        provenance=provenance,
    )


def _seed_submission_approvals(
    repo: BidRepository,
    bid: Bid,
    provenance: Provenance,
    *,
    margin_obtained: bool,
) -> None:
    OwnerDecisionService(repo.db).record_owner_decision(
        bid.bid_id,
        "PURSUE",
        "Owner approved the pursuit for readiness testing.",
        "Bid Owner",
    )
    repo.create_approval(
        _approval(
            bid,
            provenance,
            ApprovalType.MARGIN,
            obtained=margin_obtained,
        )
    )


def test_missing_required_margin_approval_is_pending_before_issue(
    bid_repo: BidRepository,
    tmp_db: Database,
    valid_bid: Bid,
    valid_provenance: Provenance,
) -> None:
    bid_repo.create_bid(valid_bid)
    _seed_submission_approvals(
        bid_repo,
        valid_bid,
        valid_provenance,
        margin_obtained=False,
    )

    report = evaluate_readiness(bid_repo, tmp_db, valid_bid.bid_id)

    assert report.bid_id == valid_bid.bid_id
    assert report.verdict == ReadinessVerdict.CLEAR
    assert report.blockers == []
    assert any(blocker.condition_id == "g4.margin_approved" for blocker in report.issue_blockers)


def test_override_writes_gate_and_audit_then_clears_bid(
    bid_repo: BidRepository,
    tmp_db: Database,
    valid_bid: Bid,
    valid_provenance: Provenance,
) -> None:
    blocked_bid = valid_bid.model_copy(
        update={"classification": BidLevel.LEVEL_0, "estimated_value": Decimal("0")}
    )
    bid_repo.create_bid(blocked_bid)
    note = "Exec accepts margin risk pending Q3 review"

    report = request_override(
        bid_repo,
        tmp_db,
        valid_bid.bid_id,
        "g0.bid_complete",
        "Executive Sponsor",
        note,
    )

    assert report.verdict == ReadinessVerdict.CLEAR
    blocker = next(
        blocker for blocker in report.blockers if blocker.condition_id == "g0.bid_complete"
    )
    assert blocker.overridden is True
    assert blocker.override_by == "Executive Sponsor"
    assert blocker.override_note == note
    assert [item.condition_id for item in report.blockers if item.overridden] == ["g0.bid_complete"]

    record = bid_repo.get_gate_record(valid_bid.bid_id, Gate.G0)
    assert record is not None
    assert record.status == GateStatus.OVERRIDDEN
    assert record.override_by == "Executive Sponsor"
    assert record.override_risk_note == note

    entries = bid_repo.list_audit(valid_bid.bid_id)
    assert len(entries) == 1
    assert entries[-1].action == "readiness_override"
    assert entries[-1].actor == "Executive Sponsor"
    assert "g0.bid_complete" in entries[-1].detail
    assert note in entries[-1].detail


def test_empty_override_note_is_rejected_before_any_write(
    bid_repo: BidRepository,
    tmp_db: Database,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)

    try:
        request_override(
            bid_repo,
            tmp_db,
            valid_bid.bid_id,
            "g0.bid_complete",
            "Executive Sponsor",
            "   ",
        )
    except ValueError as exc:
        assert str(exc) == "risk_note must be non-empty"
    else:
        raise AssertionError("request_override accepted an empty risk note")

    assert bid_repo.list_gate_records(valid_bid.bid_id) == []
    assert bid_repo.list_audit(valid_bid.bid_id) == []


def test_override_persists_across_fresh_assessment(
    bid_repo: BidRepository,
    tmp_db: Database,
    valid_bid: Bid,
    valid_provenance: Provenance,
) -> None:
    blocked_bid = valid_bid.model_copy(
        update={"classification": BidLevel.LEVEL_0, "estimated_value": Decimal("0")}
    )
    bid_repo.create_bid(blocked_bid)
    note = "Exec accepts margin risk pending Q3 review"
    request_override(
        bid_repo,
        tmp_db,
        valid_bid.bid_id,
        "g0.bid_complete",
        "Executive Sponsor",
        note,
    )

    fresh_report = evaluate_readiness(bid_repo, tmp_db, valid_bid.bid_id)

    assert fresh_report.verdict == ReadinessVerdict.CLEAR
    blocker = next(
        blocker for blocker in fresh_report.blockers if blocker.condition_id == "g0.bid_complete"
    )
    assert blocker.overridden is True
    assert blocker.override_note == note


@pytest.mark.parametrize(
    "condition_id",
    [
        "g4.margin_approved",
        "g4.required_approvals",
        "g4.high_findings_have_authority",
        "g6.senior_approvals_current",
    ],
)
def test_issue_phase_conditions_cannot_be_overridden(
    bid_repo: BidRepository,
    tmp_db: Database,
    valid_bid: Bid,
    condition_id: str,
) -> None:
    bid_repo.create_bid(valid_bid.model_copy(update={"classification": BidLevel.LEVEL_0}))

    with pytest.raises(ValueError, match="Issue-phase"):
        request_override(
            bid_repo,
            tmp_db,
            valid_bid.bid_id,
            condition_id,
            "Executive Sponsor",
            "Accept risk",
        )

    assert bid_repo.list_gate_records(valid_bid.bid_id) == []


def test_unconfirmed_finding_holds_then_confirmation_clears(
    bid_repo: BidRepository,
    tmp_db: Database,
    valid_bid: Bid,
    valid_provenance: Provenance,
) -> None:
    bid_repo.create_bid(valid_bid)
    _seed_submission_approvals(
        bid_repo,
        valid_bid,
        valid_provenance,
        margin_obtained=True,
    )
    tmp_db.create_document({"id": "DOC-READY", "filename": "proposal.pdf"})
    bid_repo.attach_document_to_bid("DOC-READY", valid_bid.bid_id)
    tmp_db.save_clause_findings(
        "DOC-READY",
        [
            {
                "pillar_id": "money",
                "findings": [
                    {
                        "finding": "Payment exposure",
                        "severity": "Medium",
                        "detail": "Commercial term needs human confirmation.",
                    }
                ],
            }
        ],
    )

    held = evaluate_readiness(bid_repo, tmp_db, valid_bid.bid_id)

    assert held.verdict == ReadinessVerdict.HOLD
    assert any(blocker.condition_id == "g5.no_unconfirmed_material" for blocker in held.blockers)

    [finding] = tmp_db.get_clause_findings("DOC-READY")
    assert tmp_db.confirm_clause_finding(finding["id"], "jason") is True

    cleared = evaluate_readiness(bid_repo, tmp_db, valid_bid.bid_id)
    assert cleared.verdict == ReadinessVerdict.CLEAR
    assert all(blocker.condition_id != "g5.no_unconfirmed_material" for blocker in cleared.blockers)


def test_level_zero_bid_without_approvals_is_clear(
    bid_repo: BidRepository,
    tmp_db: Database,
    valid_bid: Bid,
) -> None:
    level_zero_bid = valid_bid.model_copy(update={"classification": BidLevel.LEVEL_0})
    bid_repo.create_bid(level_zero_bid)

    report = evaluate_readiness(bid_repo, tmp_db, level_zero_bid.bid_id)

    assert report.verdict == ReadinessVerdict.CLEAR
    assert report.gates_blocked == []
