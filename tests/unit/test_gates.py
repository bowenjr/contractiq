from dataclasses import replace
from decimal import Decimal

import pytest

from core.enums import ApprovalType, BidLevel, Gate
from core.gates import (
    ConditionResult,
    ConditionState,
    GateContext,
    GateResult,
    evaluate_all_gates,
    evaluate_gate,
)
from core.schemas import Approval, Bid, Provenance


def _context(bid: Bid, **changes: object) -> GateContext:
    context = GateContext(
        bid=bid,
        approvals=[],
        scope_items=[],
        high_severity_findings=[],
        unconfirmed_counts={},
        prior_gate_results={},
    )
    return replace(context, **changes)


def _approval(
    bid: Bid,
    approval_type: ApprovalType,
    provenance: Provenance,
    *,
    required: bool = True,
    obtained: bool = False,
) -> Approval:
    return Approval(
        approval_id=f"APP-{approval_type.value}",
        bid_id=bid.bid_id,
        approval_type=approval_type,
        required=required,
        obtained=obtained,
        provenance=provenance,
    )


def _condition(result: GateResult, condition_id: str) -> ConditionResult:
    return next(
        condition for condition in result.conditions if condition.condition_id == condition_id
    )


def test_g0_is_met_for_complete_bid_and_unmet_for_zero_value(valid_bid: Bid) -> None:
    met = evaluate_gate(Gate.G0, _context(valid_bid))
    unmet = evaluate_gate(
        Gate.G0,
        _context(valid_bid.model_copy(update={"estimated_value": Decimal("0")})),
    )

    assert met.passed is True
    assert _condition(met, "g0.bid_complete").state == ConditionState.MET
    assert unmet.passed is False
    assert _condition(unmet, "g0.bid_complete").state == ConditionState.UNMET


def test_g1_requires_obtained_bid_no_bid_approval(
    valid_bid: Bid,
    valid_provenance: Provenance,
) -> None:
    unmet = evaluate_gate(Gate.G1, _context(valid_bid))
    approval = _approval(
        valid_bid,
        ApprovalType.BID_NO_BID,
        valid_provenance,
        obtained=True,
    )
    met = evaluate_gate(Gate.G1, _context(valid_bid, approvals=[approval]))

    assert _condition(unmet, "g1.bid_no_bid_approved").state == ConditionState.UNMET
    assert _condition(met, "g1.bid_no_bid_approved").state == ConditionState.MET


def test_g2_blocks_confirmed_included_unpriced_scope_item(valid_bid: Bid) -> None:
    result = evaluate_gate(
        Gate.G2,
        _context(
            valid_bid,
            scope_items=[
                {
                    "human_confirmed": 1,
                    "included_in_quote": 1,
                    "priced": 0,
                    "owner": "estimating",
                }
            ],
        ),
    )

    assert result.passed is False
    assert _condition(result, "g2.no_scope_gaps").state == ConditionState.UNMET


def test_g2_is_met_when_confirmed_scope_rows_are_priced_and_owned(valid_bid: Bid) -> None:
    result = evaluate_gate(
        Gate.G2,
        _context(
            valid_bid,
            scope_items=[
                {
                    "human_confirmed": 1,
                    "included_in_quote": 1,
                    "priced": 1,
                    "owner": "estimating",
                    "gap_status": "covered",
                }
            ],
        ),
    )

    assert _condition(result, "g2.no_scope_gaps").state == ConditionState.MET


def test_g2_unconfirmed_gap_rows_do_not_block(valid_bid: Bid) -> None:
    result = evaluate_gate(
        Gate.G2,
        _context(
            valid_bid,
            scope_items=[
                {
                    "human_confirmed": 0,
                    "included_in_quote": 1,
                    "priced": 0,
                    "owner": None,
                    "gap_status": "open",
                }
            ],
        ),
    )

    assert _condition(result, "g2.no_scope_gaps").state == ConditionState.MET


def test_g2_strategy_is_not_assessable_without_register(valid_bid: Bid) -> None:
    result = evaluate_gate(Gate.G2, _context(valid_bid, has_strategy_record=False))

    strategy = _condition(result, "g2.strategy_recorded")
    assert strategy.state == ConditionState.NOT_ASSESSABLE
    assert strategy.needs_register == "strategy"


def test_g3_not_assessable_passes_in_v01(valid_bid: Bid) -> None:
    result = evaluate_gate(Gate.G3, _context(valid_bid))

    assert result.passed is True
    assert result.unmet_count == 0
    assert result.not_assessable_count == 1
    assert _condition(result, "g3.suppliers_supported").state == ConditionState.NOT_ASSESSABLE


@pytest.mark.parametrize(
    ("level", "expected_state"),
    [
        (BidLevel.LEVEL_3, ConditionState.UNMET),
        (BidLevel.LEVEL_0, ConditionState.MET),
    ],
)
def test_g4_margin_rule_depends_on_bid_level(
    valid_bid: Bid,
    level: BidLevel,
    expected_state: ConditionState,
) -> None:
    bid = valid_bid.model_copy(update={"classification": level})

    result = evaluate_gate(Gate.G4, _context(bid))

    assert _condition(result, "g4.margin_approved").state == expected_state


def test_g4_required_legal_approval_blocks_when_not_obtained(
    valid_bid: Bid,
    valid_provenance: Provenance,
) -> None:
    legal = _approval(
        valid_bid,
        ApprovalType.LEGAL,
        valid_provenance,
        required=True,
        obtained=False,
    )

    result = evaluate_gate(Gate.G4, _context(valid_bid, approvals=[legal]))

    assert _condition(result, "g4.required_approvals").state == ConditionState.UNMET


@pytest.mark.parametrize(
    ("counts", "expected_state"),
    [
        ({"clause_findings": 1}, ConditionState.UNMET),
        (
            {
                "clause_findings": 0,
                "scope_items": 0,
                "obligations": 0,
                "negotiation_issues": 0,
            },
            ConditionState.MET,
        ),
    ],
)
def test_g5_unconfirmed_material_rule(
    valid_bid: Bid,
    counts: dict[str, int],
    expected_state: ConditionState,
) -> None:
    result = evaluate_gate(
        Gate.G5,
        _context(valid_bid, unconfirmed_counts=counts),
    )

    assert _condition(result, "g5.no_unconfirmed_material").state == expected_state


def test_g5_blocks_when_g4_prior_result_is_unmet(valid_bid: Bid) -> None:
    blocked_g4 = GateResult(
        gate=Gate.G4,
        passed=False,
        conditions=[
            ConditionResult(
                condition_id="g4.margin_approved",
                description="Margin approval.",
                state=ConditionState.UNMET,
            )
        ],
        unmet_count=1,
        not_assessable_count=0,
        summary="G4 BLOCKED",
    )
    passed_g2 = blocked_g4.model_copy(
        update={"gate": Gate.G2, "passed": True, "conditions": [], "unmet_count": 0}
    )
    passed_g3 = passed_g2.model_copy(update={"gate": Gate.G3})
    result = evaluate_gate(
        Gate.G5,
        _context(
            valid_bid,
            prior_gate_results={
                Gate.G2: passed_g2,
                Gate.G3: passed_g3,
                Gate.G4: blocked_g4,
            },
        ),
    )

    assert _condition(result, "g5.prior_gates_passed").state == ConditionState.UNMET


def test_g5_compliance_matrix_is_not_assessable_when_absent(valid_bid: Bid) -> None:
    result = evaluate_gate(Gate.G5, _context(valid_bid, has_compliance_matrix=False))

    condition = _condition(result, "g5.mandatory_requirements_complete")
    assert condition.state == ConditionState.NOT_ASSESSABLE
    assert condition.needs_register == "compliance_matrix"


def test_only_not_assessable_conditions_pass_and_name_missing_registers(
    valid_bid: Bid,
) -> None:
    result = evaluate_gate(Gate.G7, _context(valid_bid))

    assert result.passed is True
    assert result.not_assessable_count == 2
    assert "handover" in result.summary
    assert "reconciliation" in result.summary


def test_evaluate_all_threads_prior_gate_results_into_g5(
    valid_bid: Bid,
) -> None:
    results = evaluate_all_gates(_context(valid_bid))
    by_gate = {result.gate: result for result in results}

    assert [result.gate for result in results] == list(Gate)
    assert by_gate[Gate.G4].passed is False
    assert _condition(by_gate[Gate.G5], "g5.prior_gates_passed").state == ConditionState.UNMET


def test_gate_evaluation_is_deterministic(valid_bid: Bid) -> None:
    context = _context(valid_bid)

    assert evaluate_all_gates(context) == evaluate_all_gates(context)
