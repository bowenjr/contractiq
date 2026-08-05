"""Pure deterministic aggregation of gate results into readiness reports."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from core.enums import Gate
from core.gates import ConditionState, GateResult
from core.materiality import is_material


class ReadinessVerdict(str, Enum):  # noqa: UP042 - task specification requires str, Enum
    CLEAR = "clear"
    HOLD = "hold"
    ESCALATE = "escalate"


class Blocker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition_id: str
    gate: Gate
    description: str
    detail: str
    material: bool
    overridden: bool = False
    override_by: str | None = None
    override_note: str | None = None


class ReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bid_id: str
    verdict: ReadinessVerdict
    blockers: list[Blocker] = Field(default_factory=list)
    not_assessable: list[str] = Field(default_factory=list)
    gates_passed: list[Gate] = Field(default_factory=list)
    gates_blocked: list[Gate] = Field(default_factory=list)
    generated_at: datetime
    summary: str
    advisory: str


def _remove_satisfied_prior_gate_derivation(blockers: list[Blocker]) -> list[Blocker]:
    """Remove G5's derived cascade once all of its source risks are overridden."""
    prior_condition_id = "g5.prior_gates_passed"
    source_blockers = [
        blocker
        for blocker in blockers
        if blocker.gate in {Gate.G2, Gate.G3, Gate.G4}
        and blocker.condition_id != prior_condition_id
    ]
    if not source_blockers or not all(blocker.overridden for blocker in source_blockers):
        return blockers

    return [blocker for blocker in blockers if blocker.condition_id != prior_condition_id]


def _advisory(not_assessable_details: list[str], non_material: list[str]) -> str:
    parts: list[str] = []
    if not_assessable_details:
        parts.append("Not assessable in this build: " + "; ".join(not_assessable_details) + ".")
    else:
        parts.append("All currently defined conditions were assessable.")
    if non_material:
        parts.append("Non-material unmet conditions: " + ", ".join(non_material) + ".")
    return " ".join(parts)


def assess_readiness(
    gate_results: list[GateResult],
    overrides: dict[str, tuple[str, str]] | None = None,
    now: datetime | None = None,
) -> ReadinessReport:
    """Aggregate supplied gate results without fetching data or reading a clock."""
    if now is None:
        raise ValueError("now must be supplied by the service layer")

    applied_overrides = overrides or {}
    blockers: list[Blocker] = []
    not_assessable: list[str] = []
    not_assessable_details: list[str] = []
    non_material: list[str] = []

    for result in gate_results:
        for condition in result.conditions:
            if condition.state == ConditionState.NOT_ASSESSABLE:
                not_assessable.append(condition.condition_id)
                register = condition.needs_register or "unspecified register"
                not_assessable_details.append(f"{condition.condition_id} requires {register}")
                continue
            if condition.state != ConditionState.UNMET:
                continue

            material = is_material(condition.condition_id, condition.detail)
            if not material:
                non_material.append(condition.condition_id)
                continue
            override = applied_overrides.get(condition.condition_id)
            blockers.append(
                Blocker(
                    condition_id=condition.condition_id,
                    gate=result.gate,
                    description=condition.description,
                    detail=condition.detail,
                    material=material,
                    overridden=override is not None,
                    override_by=override[0] if override is not None else None,
                    override_note=override[1] if override is not None else None,
                )
            )

    blockers = _remove_satisfied_prior_gate_derivation(blockers)
    active_blockers = [blocker for blocker in blockers if not blocker.overridden]
    blocked_set = {blocker.gate for blocker in active_blockers}
    all_gates = list(dict.fromkeys(result.gate for result in gate_results))
    gates_blocked = [gate for gate in all_gates if gate in blocked_set]
    gates_passed = [gate for gate in all_gates if gate not in blocked_set]

    if active_blockers:
        verdict = ReadinessVerdict.HOLD
        count = len(active_blockers)
        blocker_word = "blocker" if count == 1 else "blockers"
        summary = f"Bid is on HOLD: {count} material {blocker_word} require resolution or override."
    else:
        verdict = ReadinessVerdict.CLEAR
        summary = "Bid is CLEAR: no material blockers remain."

    return ReadinessReport(
        bid_id="",
        verdict=verdict,
        blockers=blockers,
        not_assessable=not_assessable,
        gates_passed=gates_passed,
        gates_blocked=gates_blocked,
        generated_at=now,
        summary=summary,
        advisory=_advisory(not_assessable_details, non_material),
    )
