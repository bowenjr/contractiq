from datetime import UTC, datetime

from core.enums import Gate
from core.gates import ConditionResult, ConditionState, GateResult
from core.readiness import ReadinessVerdict, assess_readiness

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _result(
    gate: Gate,
    *conditions: ConditionResult,
) -> GateResult:
    unmet_count = sum(condition.state == ConditionState.UNMET for condition in conditions)
    not_assessable_count = sum(
        condition.state == ConditionState.NOT_ASSESSABLE for condition in conditions
    )
    return GateResult(
        gate=gate,
        passed=unmet_count == 0,
        conditions=list(conditions),
        unmet_count=unmet_count,
        not_assessable_count=not_assessable_count,
        summary=f"{gate.value} test result",
    )


def _condition(
    condition_id: str,
    state: ConditionState,
    *,
    register: str | None = None,
) -> ConditionResult:
    return ConditionResult(
        condition_id=condition_id,
        description=f"Description for {condition_id}",
        state=state,
        detail=f"Detail for {condition_id}",
        needs_register=register,
    )


def test_all_met_gates_are_clear() -> None:
    results = [
        _result(gate, _condition(f"{gate.value}.condition", ConditionState.MET)) for gate in Gate
    ]

    report = assess_readiness(results, now=NOW)

    assert report.verdict == ReadinessVerdict.CLEAR
    assert report.blockers == []
    assert report.gates_passed == list(Gate)
    assert report.gates_blocked == []


def test_material_scope_gap_holds_bid() -> None:
    report = assess_readiness(
        [_result(Gate.G2, _condition("g2.no_scope_gaps", ConditionState.UNMET))],
        now=NOW,
    )

    assert report.verdict == ReadinessVerdict.HOLD
    assert len(report.blockers) == 1
    assert report.blockers[0].condition_id == "g2.no_scope_gaps"
    assert report.blockers[0].material is True
    assert report.gates_blocked == [Gate.G2]


def test_not_assessable_conditions_are_advisory_not_blockers() -> None:
    results = [
        _result(
            Gate.G3,
            _condition(
                "g3.suppliers_supported",
                ConditionState.NOT_ASSESSABLE,
                register="supplier",
            ),
        ),
        _result(
            Gate.G5,
            _condition(
                "g5.mandatory_requirements_complete",
                ConditionState.NOT_ASSESSABLE,
                register="compliance_matrix",
            ),
        ),
        _result(
            Gate.G7,
            _condition(
                "g7.award_matches_offer",
                ConditionState.NOT_ASSESSABLE,
                register="reconciliation",
            ),
        ),
    ]

    report = assess_readiness(results, now=NOW)

    assert report.verdict == ReadinessVerdict.CLEAR
    assert report.blockers == []
    assert report.not_assessable == [
        "g3.suppliers_supported",
        "g5.mandatory_requirements_complete",
        "g7.award_matches_offer",
    ]
    assert "supplier" in report.advisory
    assert "compliance_matrix" in report.advisory
    assert "reconciliation" in report.advisory


def test_override_clears_only_blocker_but_keeps_risk_visible() -> None:
    report = assess_readiness(
        [_result(Gate.G2, _condition("g2.no_scope_gaps", ConditionState.UNMET))],
        overrides={"g2.no_scope_gaps": ("Executive", "Accept open interface risk")},
        now=NOW,
    )

    assert report.verdict == ReadinessVerdict.CLEAR
    assert len(report.blockers) == 1
    blocker = report.blockers[0]
    assert blocker.overridden is True
    assert blocker.override_by == "Executive"
    assert blocker.override_note == "Accept open interface risk"


def test_overriding_one_of_multiple_blockers_leaves_hold() -> None:
    results = [
        _result(Gate.G2, _condition("g2.no_scope_gaps", ConditionState.UNMET)),
        _result(Gate.G4, _condition("g4.margin_approved", ConditionState.UNMET)),
    ]

    report = assess_readiness(
        results,
        overrides={"g2.no_scope_gaps": ("Executive", "Accept scope risk")},
        now=NOW,
    )

    assert report.verdict == ReadinessVerdict.HOLD
    assert [blocker.overridden for blocker in report.blockers] == [True, False]
    assert report.gates_blocked == [Gate.G4]


def test_same_inputs_and_time_produce_identical_report() -> None:
    results = [_result(Gate.G4, _condition("g4.required_approvals", ConditionState.UNMET))]

    first = assess_readiness(results, now=NOW)
    second = assess_readiness(results, now=NOW)

    assert first == second


def test_pure_engine_requires_injected_time() -> None:
    try:
        assess_readiness([])
    except ValueError as exc:
        assert str(exc) == "now must be supplied by the service layer"
    else:
        raise AssertionError("assess_readiness accepted a missing injected time")
