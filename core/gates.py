"""Pure deterministic stage-gate rules for ContractIQ bids."""

from dataclasses import dataclass, replace
from enum import Enum

from pydantic import BaseModel, ConfigDict

from core.enums import ApprovalType, BidLevel, Gate
from core.schemas import Approval, Bid


class ConditionState(str, Enum):  # noqa: UP042 - task specification requires str, Enum
    MET = "met"
    UNMET = "unmet"
    NOT_ASSESSABLE = "not_assessable"


class ConditionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_id: str
    description: str
    state: ConditionState
    detail: str = ""
    needs_register: str | None = None


class GateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gate: Gate
    passed: bool
    conditions: list[ConditionResult]
    unmet_count: int
    not_assessable_count: int
    summary: str


@dataclass(frozen=True)
class GateContext:
    bid: Bid
    approvals: list[Approval]
    scope_items: list[dict[str, object]]
    high_severity_findings: list[dict[str, object]]
    unconfirmed_counts: dict[str, int]
    prior_gate_results: dict[Gate, GateResult]
    has_compliance_matrix: bool = False
    has_supplier_register: bool = False
    has_concession_log: bool = False
    has_reconciliation: bool = False
    has_strategy_record: bool = False


def _condition(
    condition_id: str,
    description: str,
    met: bool,
    unmet_detail: str,
) -> ConditionResult:
    return ConditionResult(
        condition_id=condition_id,
        description=description,
        state=ConditionState.MET if met else ConditionState.UNMET,
        detail="" if met else unmet_detail,
    )


def _not_assessable(
    condition_id: str,
    description: str,
    register: str,
) -> ConditionResult:
    return ConditionResult(
        condition_id=condition_id,
        description=description,
        state=ConditionState.NOT_ASSESSABLE,
        detail=f"The {register} register is not available in this build.",
        needs_register=register,
    )


def _gate_result(gate: Gate, conditions: list[ConditionResult]) -> GateResult:
    unmet_count = sum(condition.state == ConditionState.UNMET for condition in conditions)
    not_assessable = [
        condition for condition in conditions if condition.state == ConditionState.NOT_ASSESSABLE
    ]
    status = "PASSED" if unmet_count == 0 else "BLOCKED"
    summary = f"{gate.value.upper()} {status}"
    if not_assessable:
        registers = sorted(
            {
                condition.needs_register
                for condition in not_assessable
                if condition.needs_register is not None
            }
        )
        condition_word = "condition" if len(not_assessable) == 1 else "conditions"
        summary += (
            f" ({len(not_assessable)} {condition_word} not assessable in this build: "
            f"{', '.join(registers)})"
        )
    return GateResult(
        gate=gate,
        passed=unmet_count == 0,
        conditions=conditions,
        unmet_count=unmet_count,
        not_assessable_count=len(not_assessable),
        summary=summary,
    )


def _has_obtained_approval(ctx: GateContext, approval_type: ApprovalType) -> bool:
    return any(
        approval.approval_type == approval_type and approval.obtained for approval in ctx.approvals
    )


def _is_open_gap(value: object) -> bool:
    if value is None:
        return False
    normalized = str(value).strip().casefold().replace("-", "_").replace(" ", "_")
    return normalized in {"gap", "open", "open_gap", "unresolved", "unresolved_gap"}


def evaluate_g0(ctx: GateContext) -> GateResult:
    complete = bool(ctx.bid.customer.strip()) and ctx.bid.estimated_value > 0
    complete = complete and all(
        (
            ctx.bid.release_date,
            ctx.bid.customer_due_date,
            ctx.bid.internal_due_date,
        )
    )
    condition = _condition(
        "g0.bid_complete",
        "Bid record has a customer, positive estimated value, and all required dates.",
        complete,
        "Bid customer, positive estimated value, or one or more required dates are missing.",
    )
    return _gate_result(Gate.G0, [condition])


def evaluate_g1(ctx: GateContext) -> GateResult:
    approval_required = ctx.bid.classification != BidLevel.LEVEL_0
    condition = _condition(
        "g1.bid_no_bid_approved",
        "Bid/no-bid approval has been obtained.",
        not approval_required or _has_obtained_approval(ctx, ApprovalType.BID_NO_BID),
        "BID_NO_BID approval has not been obtained.",
    )
    return _gate_result(Gate.G1, [condition])


def evaluate_g2(ctx: GateContext) -> GateResult:
    confirmed_rows = [row for row in ctx.scope_items if bool(row.get("human_confirmed", False))]
    gap_rows = [
        row
        for row in confirmed_rows
        if (
            bool(row.get("included_in_quote", False))
            and (not bool(row.get("priced", False)) or not str(row.get("owner") or "").strip())
        )
        or _is_open_gap(row.get("gap_status"))
    ]
    scope_condition = _condition(
        "g2.no_scope_gaps",
        "No human-confirmed scope item is an unresolved gap.",
        not gap_rows,
        f"{len(gap_rows)} human-confirmed scope item(s) remain unresolved.",
    )
    strategy_condition = (
        ConditionResult(
            condition_id="g2.strategy_recorded",
            description="A strategy/win record exists.",
            state=ConditionState.MET,
        )
        if ctx.has_strategy_record
        else _not_assessable(
            "g2.strategy_recorded",
            "A strategy/win record exists.",
            "strategy",
        )
    )
    return _gate_result(Gate.G2, [scope_condition, strategy_condition])


def evaluate_g3(ctx: GateContext) -> GateResult:
    condition = (
        ConditionResult(
            condition_id="g3.suppliers_supported",
            description="Mandatory supplier commitments have no silence flags.",
            state=ConditionState.MET,
        )
        if ctx.has_supplier_register
        else _not_assessable(
            "g3.suppliers_supported",
            "Mandatory supplier commitments have no silence flags.",
            "supplier",
        )
    )
    return _gate_result(Gate.G3, [condition])


def evaluate_g4(ctx: GateContext) -> GateResult:
    margin_required = ctx.bid.classification in {
        BidLevel.LEVEL_2,
        BidLevel.LEVEL_3,
        BidLevel.LEVEL_4,
    }
    margin_condition = _condition(
        "g4.margin_approved",
        "Required margin approval has been obtained.",
        not margin_required or _has_obtained_approval(ctx, ApprovalType.MARGIN),
        "MARGIN approval is required for Level 2 or higher and has not been obtained.",
    )

    has_global_authority = any(
        approval.approval_type in {ApprovalType.LEGAL, ApprovalType.EXECUTIVE} and approval.obtained
        for approval in ctx.approvals
    )
    findings_without_authority = [
        finding
        for finding in ctx.high_severity_findings
        if not has_global_authority and not str(finding.get("authority_note") or "").strip()
    ]
    authority_condition = _condition(
        "g4.high_findings_have_authority",
        "Every confirmed high-severity finding has approval authority.",
        not findings_without_authority,
        (
            f"{len(findings_without_authority)} confirmed high-severity finding(s) "
            "lack approval authority."
        ),
    )

    required_types = {
        ApprovalType.LEGAL,
        ApprovalType.CREDIT,
        ApprovalType.FINANCE,
    }
    missing_required = [
        approval.approval_type.value
        for approval in ctx.approvals
        if approval.approval_type in required_types and approval.required and not approval.obtained
    ]
    required_condition = _condition(
        "g4.required_approvals",
        "All required legal, credit, and finance approvals have been obtained.",
        not missing_required,
        f"Required approval(s) not obtained: {', '.join(sorted(missing_required))}.",
    )
    return _gate_result(
        Gate.G4,
        [margin_condition, authority_condition, required_condition],
    )


def evaluate_g5(ctx: GateContext) -> GateResult:
    mandatory_condition = (
        ConditionResult(
            condition_id="g5.mandatory_requirements_complete",
            description="Every mandatory requirement is complete with evidence.",
            state=ConditionState.MET,
        )
        if ctx.has_compliance_matrix
        else _not_assessable(
            "g5.mandatory_requirements_complete",
            "Every mandatory requirement is complete with evidence.",
            "compliance_matrix",
        )
    )

    material_tables = (
        "clause_findings",
        "scope_items",
        "obligations",
        "negotiation_issues",
    )
    unconfirmed_total = sum(ctx.unconfirmed_counts.get(table, 0) for table in material_tables)
    unconfirmed_condition = _condition(
        "g5.no_unconfirmed_material",
        "No material analysis finding remains unconfirmed.",
        unconfirmed_total == 0,
        f"{unconfirmed_total} material analysis finding(s) remain unconfirmed.",
    )

    failed_prior = [
        gate.value.upper()
        for gate in (Gate.G2, Gate.G3, Gate.G4)
        if gate not in ctx.prior_gate_results or not ctx.prior_gate_results[gate].passed
    ]
    prior_condition = _condition(
        "g5.prior_gates_passed",
        "G2, G3, and G4 have passed.",
        not failed_prior,
        f"Prior gate(s) have not passed: {', '.join(failed_prior)}.",
    )
    return _gate_result(
        Gate.G5,
        [mandatory_condition, unconfirmed_condition, prior_condition],
    )


def evaluate_g6(ctx: GateContext) -> GateResult:
    condition = (
        ConditionResult(
            condition_id="g6.concessions_approved",
            description="Every post-bid concession has an approver.",
            state=ConditionState.MET,
        )
        if ctx.has_concession_log
        else _not_assessable(
            "g6.concessions_approved",
            "Every post-bid concession has an approver.",
            "concession_log",
        )
    )
    return _gate_result(Gate.G6, [condition])


def evaluate_g7(ctx: GateContext) -> GateResult:
    reconciliation_condition = (
        ConditionResult(
            condition_id="g7.award_matches_offer",
            description="PO reconciliation has no unresolved material discrepancy.",
            state=ConditionState.MET,
        )
        if ctx.has_reconciliation
        else _not_assessable(
            "g7.award_matches_offer",
            "PO reconciliation has no unresolved material discrepancy.",
            "reconciliation",
        )
    )
    handover_condition = _not_assessable(
        "g7.handover_accepted",
        "Handover has been accepted.",
        "handover",
    )
    return _gate_result(Gate.G7, [reconciliation_condition, handover_condition])


_EVALUATORS = {
    Gate.G0: evaluate_g0,
    Gate.G1: evaluate_g1,
    Gate.G2: evaluate_g2,
    Gate.G3: evaluate_g3,
    Gate.G4: evaluate_g4,
    Gate.G5: evaluate_g5,
    Gate.G6: evaluate_g6,
    Gate.G7: evaluate_g7,
}


def evaluate_gate(gate: Gate, ctx: GateContext) -> GateResult:
    """Evaluate one gate against already-assembled data."""
    return _EVALUATORS[gate](ctx)


def evaluate_all_gates(ctx: GateContext) -> list[GateResult]:
    """Evaluate G0 through G7, threading earlier results into later gates."""
    prior_results = dict(ctx.prior_gate_results)
    results: list[GateResult] = []
    for gate in Gate:
        result = evaluate_gate(
            gate,
            replace(ctx, prior_gate_results=prior_results),
        )
        results.append(result)
        prior_results[gate] = result
    return results
