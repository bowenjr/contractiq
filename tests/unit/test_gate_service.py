from core.bid_repository import BidRepository
from core.database import Database
from core.enums import ApprovalType, Gate, GateStatus
from core.gate_service import build_gate_context, evaluate_and_store_gates
from core.gates import ConditionState
from core.schemas import Approval, Bid, Provenance


def _margin_approval(
    bid: Bid,
    provenance: Provenance,
    *,
    obtained: bool,
) -> Approval:
    return Approval(
        approval_id="APP-MARGIN",
        bid_id=bid.bid_id,
        approval_type=ApprovalType.MARGIN,
        required=True,
        obtained=obtained,
        provenance=provenance,
    )


def _condition_state(
    results: list,
    gate: Gate,
    condition_id: str,
) -> ConditionState:
    result = next(result for result in results if result.gate == gate)
    condition = next(
        condition for condition in result.conditions if condition.condition_id == condition_id
    )
    return condition.state


def test_margin_approval_re_evaluation_flips_g4_record(
    bid_repo: BidRepository,
    tmp_db: Database,
    valid_bid: Bid,
    valid_provenance: Provenance,
) -> None:
    bid_repo.create_bid(valid_bid)
    approval = _margin_approval(valid_bid, valid_provenance, obtained=False)
    bid_repo.create_approval(approval)

    evaluate_and_store_gates(bid_repo, tmp_db, valid_bid.bid_id)

    blocked = bid_repo.get_gate_record(valid_bid.bid_id, Gate.G4)
    assert blocked is not None
    assert blocked.status == GateStatus.IN_REVIEW
    assert blocked.blockers

    bid_repo.update_approval(approval.model_copy(update={"obtained": True}))
    evaluate_and_store_gates(bid_repo, tmp_db, valid_bid.bid_id)

    passed = bid_repo.get_gate_record(valid_bid.bid_id, Gate.G4)
    assert passed is not None
    assert passed.status == GateStatus.PASSED
    assert passed.blockers == []


def test_unconfirmed_finding_blocks_g5_until_confirmed(
    bid_repo: BidRepository,
    tmp_db: Database,
    valid_bid: Bid,
    valid_provenance: Provenance,
) -> None:
    bid_repo.create_bid(valid_bid)
    tmp_db.create_document({"id": "DOC-GATE", "filename": "contract.pdf"})
    bid_repo.attach_document_to_bid("DOC-GATE", valid_bid.bid_id)
    bid_repo.create_approval(_margin_approval(valid_bid, valid_provenance, obtained=True))
    tmp_db.save_clause_findings(
        "DOC-GATE",
        [
            {
                "pillar_id": "money",
                "findings": [
                    {
                        "finding": "Payment term",
                        "severity": "Medium",
                        "detail": "Unconfirmed commercial concern.",
                    }
                ],
            }
        ],
    )

    blocked_results = evaluate_and_store_gates(bid_repo, tmp_db, valid_bid.bid_id)
    assert (
        _condition_state(
            blocked_results,
            Gate.G5,
            "g5.no_unconfirmed_material",
        )
        == ConditionState.UNMET
    )
    blocked = bid_repo.get_gate_record(valid_bid.bid_id, Gate.G5)
    assert blocked is not None
    assert "No material analysis finding remains unconfirmed." in blocked.blockers

    [finding] = tmp_db.get_clause_findings("DOC-GATE")
    tmp_db.confirm_clause_finding(finding["id"], "jason")
    cleared_results = evaluate_and_store_gates(bid_repo, tmp_db, valid_bid.bid_id)

    assert (
        _condition_state(
            cleared_results,
            Gate.G5,
            "g5.no_unconfirmed_material",
        )
        == ConditionState.MET
    )
    cleared = bid_repo.get_gate_record(valid_bid.bid_id, Gate.G5)
    assert cleared is not None
    assert "No material analysis finding remains unconfirmed." not in cleared.blockers


def test_absent_requirements_table_is_not_assessable_and_does_not_block(
    bid_repo: BidRepository,
    tmp_db: Database,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)

    context = build_gate_context(bid_repo, tmp_db, valid_bid.bid_id)
    results = evaluate_and_store_gates(bid_repo, tmp_db, valid_bid.bid_id)

    assert context.has_compliance_matrix is False
    assert (
        _condition_state(
            results,
            Gate.G5,
            "g5.mandatory_requirements_complete",
        )
        == ConditionState.NOT_ASSESSABLE
    )


def test_one_audit_entry_is_written_per_evaluation(
    bid_repo: BidRepository,
    tmp_db: Database,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)

    evaluate_and_store_gates(
        bid_repo,
        tmp_db,
        valid_bid.bid_id,
        actor="jason",
    )
    evaluate_and_store_gates(
        bid_repo,
        tmp_db,
        valid_bid.bid_id,
        actor="jason",
    )

    entries = bid_repo.list_audit(valid_bid.bid_id)
    assert len(entries) == 2
    assert all(entry.action == "gates_evaluated" for entry in entries)
    assert all(entry.actor == "jason" for entry in entries)
