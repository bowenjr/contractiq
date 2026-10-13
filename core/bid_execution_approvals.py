"""Read-only approval classification for Stage 3 submission readiness.

This module does not grant authority or write approval state.  It translates the
existing gate and senior-approval readers into presentation facts whose severity
depends on whether a recorded rule or decision establishes the requirement.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import cast

from pydantic import BaseModel, ConfigDict

from core.bid_control_center import blocker_destination
from core.bid_repository import BidRepository
from core.database import Database
from core.enums import ApprovalType, BidLevel, Gate
from core.gate_service import assess_bid_senior_approvals
from core.readiness import Blocker, ReadinessReport
from core.readiness_service import evaluate_readiness
from core.schemas import Approval
from core.senior_approvals import SeniorApprovalAssessment, SeniorApprovalRequirement


class ApprovalApplicability(StrEnum):
    """Why an approval-related readiness fact applies to this Bid."""

    DEVIATION_APPROVAL_REQUIRED = "Deviation approval required"
    ROUTINE_AUTHORIZATION_REQUIRED = "Routine authorization required"
    NEEDS_ASSESSMENT = "Needs assessment"
    NOT_APPLICABLE = "Not applicable"
    COMPLETED = "Completed"


class ApprovalRequirementStatus(StrEnum):
    """Recorded state of one classified approval requirement."""

    MISSING = "Missing"
    OBTAINED = "Obtained"
    NEEDS_ASSESSMENT = "Needs assessment"
    NOT_APPLICABLE = "Not applicable"
    COMPLETED_BY_OVERRIDE = "Completed by override"


class ApprovalRequirementFact(BaseModel):
    """One approval requirement shown by Stage 3 readiness."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_key: str
    classification: ApprovalApplicability
    heading: str
    establishing_basis: str
    status: ApprovalRequirementStatus
    detail: str
    resolve_label: str
    resolve_url: str


_APPROVAL_CONDITION_IDS = {
    "g4.margin_approved",
    "g4.required_approvals",
    "g4.high_findings_have_authority",
    "g6.senior_approvals_current",
}
_ROUTINE_G4_TYPES = {
    ApprovalType.LEGAL,
    ApprovalType.CREDIT,
    ApprovalType.FINANCE,
}
_SENIOR_LEVELS = {BidLevel.LEVEL_2, BidLevel.LEVEL_3, BidLevel.LEVEL_4}


def _approval_blockers(report: ReadinessReport) -> dict[str, Blocker]:
    return {
        item.condition_id: item
        for item in (*report.blockers, *report.issue_blockers)
        if item.condition_id in _APPROVAL_CONDITION_IDS
    }


def _destination(bid_id: str, condition_id: str, gate: Gate) -> str:
    return blocker_destination(bid_id, condition_id, gate)


def _fact(
    *,
    bid_id: str,
    requirement_key: str,
    classification: ApprovalApplicability,
    heading: str,
    establishing_basis: str,
    status: ApprovalRequirementStatus,
    detail: str,
    condition_id: str,
    gate: Gate,
) -> ApprovalRequirementFact:
    return ApprovalRequirementFact(
        requirement_key=requirement_key,
        classification=classification,
        heading=heading,
        establishing_basis=establishing_basis,
        status=status,
        detail=detail,
        resolve_label=(
            "Review recorded approval"
            if classification
            in {
                ApprovalApplicability.COMPLETED,
                ApprovalApplicability.NOT_APPLICABLE,
            }
            else "Resolve approval requirement"
        ),
        resolve_url=_destination(bid_id, condition_id, gate),
    )


def _completed_or_required(
    *,
    bid_id: str,
    requirement: SeniorApprovalRequirement,
    classification: ApprovalApplicability,
    heading: str,
    establishing_basis: str,
    satisfied: bool,
    overridden: bool,
    condition_id: str,
    gate: Gate,
) -> ApprovalRequirementFact:
    if overridden:
        return _fact(
            bid_id=bid_id,
            requirement_key=requirement.key,
            classification=ApprovalApplicability.COMPLETED,
            heading=heading,
            establishing_basis=establishing_basis,
            status=ApprovalRequirementStatus.COMPLETED_BY_OVERRIDE,
            detail="The recorded gate condition was completed by an existing override.",
            condition_id=condition_id,
            gate=gate,
        )
    if satisfied:
        return _fact(
            bid_id=bid_id,
            requirement_key=requirement.key,
            classification=ApprovalApplicability.COMPLETED,
            heading=heading,
            establishing_basis=establishing_basis,
            status=ApprovalRequirementStatus.OBTAINED,
            detail="Current recorded approval evidence satisfies this requirement.",
            condition_id=condition_id,
            gate=gate,
        )
    return _fact(
        bid_id=bid_id,
        requirement_key=requirement.key,
        classification=classification,
        heading=heading,
        establishing_basis=establishing_basis,
        status=ApprovalRequirementStatus.MISSING,
        detail="The established approval requirement has no current recorded outcome or evidence.",
        condition_id=condition_id,
        gate=gate,
    )


def _configuration_fact(
    bid_id: str,
    assessment: SeniorApprovalAssessment,
    overridden: bool,
) -> ApprovalRequirementFact | None:
    if assessment.policy_effective and assessment.pricing_basis_present:
        return None
    if overridden:
        return _fact(
            bid_id=bid_id,
            requirement_key="senior-approval:configuration",
            classification=ApprovalApplicability.COMPLETED,
            heading="Senior approval assessment",
            establishing_basis="Senior approval configuration check",
            status=ApprovalRequirementStatus.COMPLETED_BY_OVERRIDE,
            detail="The recorded gate condition was completed by an existing override.",
            condition_id="g6.senior_approvals_current",
            gate=Gate.G6,
        )
    if not assessment.policy_effective and not assessment.pricing_basis_present:
        detail = (
            "No authority policy or accepted pricing basis is recorded, so ContractIQ cannot "
            "determine which approvals apply."
        )
    elif not assessment.policy_effective:
        detail = (
            "No effective authority policy is recorded, so ContractIQ cannot determine the "
            "required approver."
        )
    else:
        detail = (
            "No accepted pricing basis is recorded, so ContractIQ cannot determine whether "
            "the recorded approval evidence is current."
        )
    return _fact(
        bid_id=bid_id,
        requirement_key="senior-approval:configuration",
        classification=ApprovalApplicability.NEEDS_ASSESSMENT,
        heading="Needs assessment",
        establishing_basis="Senior approval applicability check",
        status=ApprovalRequirementStatus.NEEDS_ASSESSMENT,
        detail=detail,
        condition_id="g6.senior_approvals_current",
        gate=Gate.G6,
    )


def classify_approval_requirements(
    *,
    bid_id: str,
    bid_level: BidLevel,
    readiness: ReadinessReport,
    assessment: SeniorApprovalAssessment,
    approvals: Sequence[Approval],
    commercial_outcomes: Mapping[str, str],
    route_approvers: Mapping[str, tuple[str, ...]] | None = None,
) -> list[ApprovalRequirementFact]:
    """Classify already-read approval facts without I/O or authority decisions."""
    blockers = _approval_blockers(readiness)
    missing = set(assessment.missing_requirement_keys)
    recorded_approvers = route_approvers or {}
    facts: list[ApprovalRequirementFact] = []
    g6_overridden = bool(
        (senior := blockers.get("g6.senior_approvals_current")) and senior.overridden
    )

    for requirement in assessment.requirements:
        if requirement.key == "bid-level:margin":
            condition_id = "g4.margin_approved"
            blocker = blockers.get(condition_id)
            facts.append(
                _completed_or_required(
                    bid_id=bid_id,
                    requirement=requirement,
                    classification=ApprovalApplicability.ROUTINE_AUTHORIZATION_REQUIRED,
                    heading="Margin approval required",
                    establishing_basis=(
                        f"Level {bid_level.value.removeprefix('level_')} governance rule"
                    ),
                    satisfied=requirement.key not in missing,
                    overridden=bool(blocker and blocker.overridden),
                    condition_id=condition_id,
                    gate=Gate.G4,
                )
            )
            continue

        if requirement.key.startswith("approval-type:"):
            if requirement.approval_type is ApprovalType.MARGIN and bid_level in _SENIOR_LEVELS:
                continue
            approval_type = requirement.approval_type
            label = approval_type.value.replace("_", " ").title() if approval_type else "Recorded"
            condition_id = (
                "g4.required_approvals"
                if approval_type in _ROUTINE_G4_TYPES
                else "g6.senior_approvals_current"
            )
            blocker = blockers.get(condition_id)
            row_obtained = any(
                row.approval_type is approval_type and row.required and row.obtained
                for row in approvals
            )
            facts.append(
                _completed_or_required(
                    bid_id=bid_id,
                    requirement=requirement,
                    classification=ApprovalApplicability.ROUTINE_AUTHORIZATION_REQUIRED,
                    heading=f"{label} approval required",
                    establishing_basis=requirement.source,
                    satisfied=requirement.key not in missing and row_obtained,
                    overridden=bool(blocker and blocker.overridden) or g6_overridden,
                    condition_id=condition_id,
                    gate=Gate.G4 if condition_id.startswith("g4.") else Gate.G6,
                )
            )
            continue

        if requirement.key.startswith("commercial-role:"):
            role = requirement.required_approver or "recorded approver"
            position_id = requirement.subject_id or ""
            satisfied = requirement.key not in missing or bool(
                commercial_outcomes.get(position_id, "").strip()
            )
            facts.append(
                _completed_or_required(
                    bid_id=bid_id,
                    requirement=requirement,
                    classification=ApprovalApplicability.DEVIATION_APPROVAL_REQUIRED,
                    heading=f"Approval required from {role}",
                    establishing_basis=f"Commercial position requires {role}",
                    satisfied=satisfied,
                    overridden=g6_overridden,
                    condition_id="g6.senior_approvals_current",
                    gate=Gate.G6,
                )
            )
            continue

        if requirement.key.startswith("approval-route:"):
            route_id = requirement.route_id or ""
            roles = recorded_approvers.get(route_id, ())
            role_label = " / ".join(roles)
            facts.append(
                _completed_or_required(
                    bid_id=bid_id,
                    requirement=requirement,
                    classification=ApprovalApplicability.DEVIATION_APPROVAL_REQUIRED,
                    heading=(
                        f"Approval required from {role_label}" if roles else "Open approval route"
                    ),
                    establishing_basis=(
                        f"Approval route requires {role_label}" if roles else requirement.source
                    ),
                    satisfied=requirement.key not in missing,
                    overridden=g6_overridden,
                    condition_id="g6.senior_approvals_current",
                    gate=Gate.G6,
                )
            )

    high_authority = blockers.get("g4.high_findings_have_authority")
    if high_authority is not None:
        count = high_authority.detail.split(" ", 1)[0]
        requirement = SeniorApprovalRequirement(
            key="g4.high_findings_have_authority",
            source="Confirmed high-severity finding lacks recorded approval authority",
        )
        facts.append(
            _completed_or_required(
                bid_id=bid_id,
                requirement=requirement,
                classification=ApprovalApplicability.DEVIATION_APPROVAL_REQUIRED,
                heading="High-severity finding needs approval authority",
                establishing_basis="Confirmed high-severity finding requires authority",
                satisfied=False,
                overridden=high_authority.overridden,
                condition_id="g4.high_findings_have_authority",
                gate=Gate.G4,
            ).model_copy(
                update={
                    "detail": (
                        f"{count} confirmed high-severity finding(s) lack recorded approval "
                        "authority."
                    )
                }
            )
        )

    configuration = _configuration_fact(bid_id, assessment, g6_overridden)
    if assessment.requirements and configuration is not None:
        facts.append(configuration)

    if not facts:
        facts.append(
            _fact(
                bid_id=bid_id,
                requirement_key="approval:not-applicable",
                classification=ApprovalApplicability.NOT_APPLICABLE,
                heading="No additional approval applies",
                establishing_basis=(
                    f"Level {bid_level.value.removeprefix('level_')} governance rule and "
                    "recorded Bid facts"
                ),
                status=ApprovalRequirementStatus.NOT_APPLICABLE,
                detail="No approval requirement is derived for this Bid level and recorded facts.",
                condition_id="g4.required_approvals",
                gate=Gate.G4,
            )
        )
    return sorted(facts, key=lambda item: (item.heading.casefold(), item.requirement_key))


def _conn(db: Database) -> sqlite3.Connection:
    return cast(sqlite3.Connection, db._conn())


def _current_commercial_outcomes(db: Database, bid_id: str) -> dict[str, str]:
    """Read current outcomes without creating or changing a commercial register."""
    with _conn(db) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='commercial_positions'"
        ).fetchone()
        if table is None:
            return {}
        rows = conn.execute(
            """SELECT p.position_id,v.current_outcome
            FROM commercial_positions p
            JOIN commercial_position_versions v ON v.position_id=p.position_id
              AND v.version_number=p.current_version
            WHERE p.bid_id=?""",
            (bid_id,),
        ).fetchall()
    return {str(row["position_id"]): str(row["current_outcome"] or "") for row in rows}


def _open_route_approvers(db: Database, bid_id: str) -> dict[str, tuple[str, ...]]:
    """Return exact recorded route roles without inventing an escalation role."""
    with _conn(db) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='approval_routes'"
        ).fetchone()
        if table is None:
            return {}
        rows = conn.execute(
            "SELECT route_id,requirements_json FROM approval_routes WHERE bid_id=?",
            (bid_id,),
        ).fetchall()
    result: dict[str, tuple[str, ...]] = {}
    for row in rows:
        try:
            requirements = json.loads(str(row["requirements_json"] or "[]"))
        except json.JSONDecodeError:
            requirements = []
        if not isinstance(requirements, list):
            continue
        roles = tuple(
            dict.fromkeys(
                role
                for item in requirements
                if isinstance(item, dict)
                if (role := str(item.get("role_code") or "").strip())
            )
        )
        if roles:
            result[str(row["route_id"])] = roles
    return result


def read_approval_requirement_facts(
    repo: BidRepository,
    db: Database,
    bid_id: str,
    *,
    as_of: date,
) -> list[ApprovalRequirementFact]:
    """Build Stage 3 approval facts through existing read-only approval seams."""
    bid = repo.get_bid(bid_id)
    if bid is None:
        raise ValueError(f"Bid not found: {bid_id}")
    as_of_datetime = datetime.combine(as_of, time.min, tzinfo=UTC)
    readiness = evaluate_readiness(repo, db, bid_id)
    assessment = assess_bid_senior_approvals(
        repo,
        db,
        bid_id,
        as_of=as_of_datetime,
    )
    approvals = repo.list_current_approvals(bid_id)
    return classify_approval_requirements(
        bid_id=bid_id,
        bid_level=bid.classification,
        readiness=readiness,
        assessment=assessment,
        approvals=approvals,
        commercial_outcomes=_current_commercial_outcomes(db, bid_id),
        route_approvers=_open_route_approvers(db, bid_id),
    )


__all__ = [
    "ApprovalApplicability",
    "ApprovalRequirementFact",
    "ApprovalRequirementStatus",
    "classify_approval_requirements",
    "read_approval_requirement_facts",
]
