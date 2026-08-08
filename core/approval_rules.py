"""Pure policy matching and approval gaps."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from core.approval_authority import AuthorityPolicy, RouteRequirement, StageMode


class RouteEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)
    matched_rule_ids: tuple[str, ...]
    requirements: tuple[RouteRequirement, ...]
    explanation: str


def match_policy(policy: AuthorityPolicy, facts: dict[str, Any]) -> RouteEvaluation:
    matched = []
    for rule in policy.rules:
        if all(facts.get(key) == value for key, value in rule.get("dimensions", {}).items()):
            matched.append(str(rule.get("rule_id", "")))
    if not matched:
        raise ValueError("no authority rule matched")
    requirements = []
    for stage in sorted(policy.stages, key=lambda item: int(item.get("order", 0))):
        mode = StageMode(str(stage.get("mode", "ALL_REQUIRED")))
        for role in stage.get("roles", []):
            requirements.append(
                RouteRequirement(
                    route_id="PENDING",
                    stage_order=int(stage["order"]),
                    stage_mode=mode,
                    role_code=str(role),
                )
            )
    return RouteEvaluation(
        matched_rule_ids=tuple(matched),
        requirements=tuple(requirements),
        explanation="Deterministic structured policy match",
    )


GAP_CODES = (
    "APPROVAL_POLICY_NOT_CONFIGURED",
    "APPROVAL_POLICY_NOT_EFFECTIVE",
    "APPROVAL_POLICY_INVALID",
    "APPROVAL_ROUTE_NOT_DETERMINED",
    "APPROVAL_ROUTE_AMBIGUOUS",
    "APPROVAL_CASE_MISSING",
    "APPROVAL_PACKAGE_MISSING",
    "APPROVAL_PACKAGE_INCOMPLETE",
    "APPROVAL_PACKAGE_NOT_LATEST",
    "APPROVAL_SUBJECT_MISSING",
    "APPROVAL_SUBJECT_WRONG_BID",
    "APPROVAL_SUBJECT_UNHEALTHY",
    "APPROVAL_SUBJECT_NOT_CURRENT",
    "APPROVAL_SUBJECT_NOT_ACCEPTED",
    "APPROVAL_REQUIRED_ROLE_UNASSIGNED",
    "APPROVAL_ROLE_ASSIGNMENT_NOT_EFFECTIVE",
    "APPROVAL_SEPARATION_OF_DUTIES_UNMET",
    "APPROVAL_PENDING",
    "APPROVAL_OVERDUE",
    "APPROVAL_ABSTAINED",
    "APPROVAL_REJECTED",
    "APPROVAL_CHANGES_REQUIRED",
    "APPROVAL_EXPIRED",
    "APPROVAL_REVOKED",
    "APPROVAL_SUPERSEDED",
    "APPROVAL_STALE_SOURCE",
    "APPROVAL_REQUIREMENT_EXCEPTION_UNCOVERED",
    "APPROVAL_SUPPLIER_EXCEPTION_UNCOVERED",
    "APPROVAL_DELIVERABLE_EXCEPTION_UNCOVERED",
    "APPROVAL_COMMERCIAL_EXCEPTION_UNCOVERED",
    "APPROVAL_CONTRACT_RISK_UNCOVERED",
)


def approval_gaps(
    cases: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    policies: list[dict[str, Any]],
    *,
    as_of: datetime,
) -> list[dict[str, str]]:
    result = []

    def add(code: str, bid: str, case: str, reason: str) -> None:
        result.append(
            {
                "code": code,
                "bid_id": bid,
                "case_id": case,
                "severity": "BLOCKING_ATTENTION",
                "explanation": reason,
                "dedup_key": f"{case}:{code}",
            }
        )

    published = [p for p in policies if p.get("lifecycle_state") == "PUBLISHED"]
    if not published:
        add(
            "APPROVAL_POLICY_NOT_CONFIGURED",
            "",
            "",
            "No published authority policy is configured; approval is blocked safely.",
        )
    effective = [
        policy
        for policy in published
        if str(policy.get("effective_from", "")) <= as_of.isoformat()
        and (
            not policy.get("effective_until") or str(policy["effective_until"]) > as_of.isoformat()
        )
    ]
    for case in cases:
        if case.get("lifecycle_state") != "ACTIVE":
            continue
        bid = str(case["bid_id"])
        cid = str(case["case_id"])
        route = next((r for r in routes if r.get("case_id") == cid), None)
        if not effective:
            add(
                "APPROVAL_POLICY_NOT_EFFECTIVE",
                bid,
                cid,
                "No published authority policy is effective.",
            )
            continue
        if route is None:
            add("APPROVAL_ROUTE_NOT_DETERMINED", bid, cid, "No frozen approval route exists.")
            continue
        if route.get("state") == "PENDING":
            add("APPROVAL_PENDING", bid, cid, "Required approval stages remain pending.")
        if route.get("state") == "REJECTED":
            add("APPROVAL_REJECTED", bid, cid, "Approval route was rejected.")
        if route.get("state") == "CHANGES_REQUIRED":
            add("APPROVAL_CHANGES_REQUIRED", bid, cid, "Approval route requires changes.")
        if (
            route.get("approval_valid_until")
            and str(route["approval_valid_until"]) < as_of.isoformat()
        ):
            add("APPROVAL_EXPIRED", bid, cid, "Approval validity has expired.")
    return sorted(result, key=lambda row: (row["bid_id"], row["case_id"], row["code"]))
