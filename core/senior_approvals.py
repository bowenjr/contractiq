"""Pure derivation and assessment of issue-phase senior approvals.

Level 2+ margin approval is a temporary governance-level rule. No monetary-value
or margin-percentage threshold is evaluated here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from core.approval_rules import APPROVAL_POLICY_NOT_CONFIGURED, effective_policies
from core.enums import ApprovalType, BidLevel

_SENIOR_LEVELS = {BidLevel.LEVEL_2, BidLevel.LEVEL_3, BidLevel.LEVEL_4}
_OPEN_ROUTE_STATES = {"PENDING", "IN_REVIEW", "CHANGES_REQUIRED", "REJECTED"}


class SeniorApprovalRequirement(BaseModel):
    """One independently derived approval obligation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    source: str
    approval_type: ApprovalType | None = None
    required_approver: str | None = None
    subject_id: str | None = None
    route_id: str | None = None


class SeniorApprovalAssessment(BaseModel):
    """Deterministic issue-phase result over derived obligations and evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requirements: tuple[SeniorApprovalRequirement, ...]
    missing_requirement_keys: tuple[str, ...]
    policy_effective: bool
    pricing_basis_present: bool
    clear: bool
    detail: str


def derive_required_approvals(
    bid_level: BidLevel,
    commercial_positions: Sequence[Mapping[str, object]],
    approval_rows: Sequence[Mapping[str, object]],
    approval_routes: Sequence[Mapping[str, object]],
) -> tuple[SeniorApprovalRequirement, ...]:
    """Derive requirements from level, terms, required rows, and open routes."""
    derived: dict[str, SeniorApprovalRequirement] = {}
    if bid_level in _SENIOR_LEVELS:
        item = SeniorApprovalRequirement(
            key="bid-level:margin",
            source=(
                f"{bid_level.value} requires margin approval under the temporary "
                "governance-level rule; value and margin thresholds are not enforced"
            ),
            approval_type=ApprovalType.MARGIN,
        )
        derived[item.key] = item

    for row in commercial_positions:
        position_id = str(row.get("position_id") or "unknown")
        approver = str(row.get("required_approver") or "").strip()
        if approver:
            key = f"commercial-role:{position_id}:{approver.casefold()}"
            derived[key] = SeniorApprovalRequirement(
                key=key,
                source=f"Commercial position {position_id} requires {approver}",
                required_approver=approver,
                subject_id=position_id,
            )
    for row in approval_rows:
        if not bool(row.get("required")):
            continue
        approval_type = ApprovalType(str(row["approval_type"]))
        key = f"approval-type:{approval_type.value}"
        derived[key] = SeniorApprovalRequirement(
            key=key,
            source=f"Existing {approval_type.value} approval row is required",
            approval_type=approval_type,
        )

    for row in approval_routes:
        if str(row.get("state")) not in _OPEN_ROUTE_STATES:
            continue
        route_id = str(row["route_id"])
        key = f"approval-route:{route_id}"
        derived[key] = SeniorApprovalRequirement(
            key=key,
            source=f"Approval route {route_id} remains open",
            route_id=route_id,
        )
    return tuple(derived[key] for key in sorted(derived))


def _route_roles(route: Mapping[str, object]) -> set[str]:
    raw = route.get("requirements_json")
    try:
        values = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return set()
    return {
        str(item.get("role_code") or "").strip().casefold()
        for item in values
        if isinstance(item, dict)
    }


def _route_subject_ids(route: Mapping[str, object]) -> set[str]:
    raw = route.get("subject_ids")
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return set()
    return {str(value) for value in raw}


def _json_roles(raw: object) -> set[str]:
    try:
        values = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return set()
    if not isinstance(values, list):
        return set()
    roles: set[str] = set()
    for value in values:
        if isinstance(value, str):
            roles.add(value.strip().casefold())
        elif isinstance(value, dict):
            stage_roles = value.get("roles", [])
            if isinstance(stage_roles, list):
                roles.update(str(role).strip().casefold() for role in stage_roles)
    return roles


def _policy_roles(policy: Mapping[str, object]) -> set[str]:
    return _json_roles(policy.get("roles_json")) | _json_roles(policy.get("stages_json"))


def _approval_has_evidence(row: Mapping[str, object], baseline_id: str) -> bool:
    return (
        bool(row.get("obtained"))
        and all(
            str(row.get(field) or "").strip() for field in ("authority", "evidence_ref", "decision")
        )
        and row.get("pricing_baseline_id") == baseline_id
    )


def _approval_is_policy_covered(
    row: Mapping[str, object], policies: Sequence[Mapping[str, object]]
) -> bool:
    approval_type = str(row.get("approval_type") or "").strip().casefold()
    authority = str(row.get("authority") or "").strip().casefold()
    return any(
        approval_type in _policy_roles(policy) or authority in _policy_roles(policy)
        for policy in policies
    )


def _route_is_policy_covered(
    route: Mapping[str, object], policies: Sequence[Mapping[str, object]]
) -> bool:
    effective_ids = {str(policy.get("policy_id")) for policy in policies}
    if str(route.get("policy_id") or "") not in effective_ids:
        return False
    route_roles = _route_roles(route)
    policy = next(
        policy for policy in policies if str(policy.get("policy_id")) == str(route.get("policy_id"))
    )
    return bool(route_roles & _policy_roles(policy))


def _is_satisfied(
    requirement: SeniorApprovalRequirement,
    approvals: Sequence[Mapping[str, object]],
    routes: Sequence[Mapping[str, object]],
    policies: Sequence[Mapping[str, object]],
    pricing_baseline_id: str | None,
) -> bool:
    if pricing_baseline_id is None:
        return False
    obtained = [
        row
        for row in approvals
        if _approval_has_evidence(row, pricing_baseline_id)
        and _approval_is_policy_covered(row, policies)
    ]
    approved_routes = [
        row
        for row in routes
        if row.get("state") == "APPROVED" and _route_is_policy_covered(row, policies)
    ]
    if requirement.route_id is not None:
        return any(row.get("route_id") == requirement.route_id for row in approved_routes)
    if requirement.approval_type is not None:
        return any(
            row.get("approval_type") == requirement.approval_type.value for row in obtained
        ) or any(
            requirement.approval_type is ApprovalType.MARGIN
            and row.get("decision_type") == "PRICE_AND_MARGIN"
            and "margin" in _route_roles(row)
            for row in approved_routes
        )
    if requirement.required_approver is not None:
        role = requirement.required_approver.casefold()
        return any(str(row.get("authority") or "").casefold() == role for row in obtained) or any(
            role in _route_roles(row) for row in approved_routes
        )
    if requirement.subject_id is not None:
        return any(
            requirement.subject_id in _route_subject_ids(row) for row in approved_routes
        ) or any(
            row.get("approval_type") in {ApprovalType.LEGAL.value, ApprovalType.EXECUTIVE.value}
            for row in obtained
        )
    return False


def assess_senior_approvals(
    *,
    bid_level: BidLevel,
    commercial_positions: Sequence[Mapping[str, object]],
    approval_rows: Sequence[Mapping[str, object]],
    approval_routes: Sequence[Mapping[str, object]],
    policies: list[dict[str, Any]],
    pricing_baseline_id: str | None,
    as_of: datetime,
) -> SeniorApprovalAssessment:
    """Assess baseline-bound evidence against effective authority-policy roles."""
    requirements = derive_required_approvals(
        bid_level, commercial_positions, approval_rows, approval_routes
    )
    if not requirements:
        return SeniorApprovalAssessment(
            requirements=(),
            missing_requirement_keys=(),
            policy_effective=bool(effective_policies(policies, as_of=as_of)),
            pricing_basis_present=pricing_baseline_id is not None,
            clear=True,
            detail="No senior approval requirement is derived.",
        )
    policy_effective = bool(effective_policies(policies, as_of=as_of))
    effective = effective_policies(policies, as_of=as_of)
    missing = tuple(
        item.key
        for item in requirements
        if not _is_satisfied(item, approval_rows, approval_routes, effective, pricing_baseline_id)
    )
    problems: list[str] = []
    if not policy_effective:
        problems.append(
            f"{APPROVAL_POLICY_NOT_CONFIGURED}: authority policy not published or effective; "
            "publish an effective authority policy"
        )
    if not pricing_baseline_id:
        problems.append("Pricing basis missing; select an accepted pricing-scenario baseline")
    legacy = [
        str(row.get("approval_id") or "unknown")
        for row in approval_rows
        if bool(row.get("obtained")) and row.get("pricing_baseline_id") is None
    ]
    stale = [
        str(row.get("approval_id") or "unknown")
        for row in approval_rows
        if bool(row.get("obtained"))
        and row.get("pricing_baseline_id") is not None
        and row.get("pricing_baseline_id") != pricing_baseline_id
    ]
    if legacy:
        problems.append(
            "Approval record(s) "
            + ", ".join(legacy)
            + " were recorded before a pricing baseline was identified"
        )
    if stale:
        problems.append(
            "Approval record(s) "
            + ", ".join(stale)
            + " identify a previously selected pricing baseline"
        )
    if missing:
        problems.append("Missing current approval evidence for: " + ", ".join(missing))
    return SeniorApprovalAssessment(
        requirements=requirements,
        missing_requirement_keys=missing,
        policy_effective=policy_effective,
        pricing_basis_present=pricing_baseline_id is not None,
        clear=not problems,
        detail="; ".join(problems) or "All derived senior approvals are current.",
    )
