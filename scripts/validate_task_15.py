"""Synthetic deterministic TASK-15 acceptance oracle."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.approval_authority import (
    ApprovalEvent,
    AuthorityPolicy,
    CaseLifecycle,
    DecisionCase,
    DecisionPackage,
    DecisionType,
    EventDecision,
    PolicyLifecycle,
    RoleAssignment,
    RouteCycle,
    StageMode,
    SubjectLink,
    SubjectType,
)
from core.approval_repository import ApprovalRepository
from core.approval_service import ApprovalService
from core.database import Database
from core.enums import Actor
from core.schemas import Provenance


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="contractiq-task15-") as root:
        now = datetime.now(UTC)
        db = Database(Path(root) / "approval.db")
        repo = ApprovalRepository(db)
        service = ApprovalService(repo)
        provenance = Provenance(
            created_by=Actor.HUMAN, confirmed_by="validator", human_confirmed=True
        )
        policy = AuthorityPolicy(
            name="Synthetic residual risk authority",
            description="Synthetic only",
            scope="BID",
            effective_from=now - timedelta(minutes=1),
            lifecycle_state=PolicyLifecycle.DRAFT,
            roles=("RISK_REVIEWER",),
            rules=(
                {"rule_id": "RULE-RISK", "dimensions": {"decision_type": "RESIDUAL_CONTRACT_RISK"}},
            ),
            stages=(
                {"order": 1, "mode": StageMode.ALL_REQUIRED.value, "roles": ["RISK_REVIEWER"]},
            ),
            created_by="validator",
            created_at=now,
            provenance=provenance,
        )
        service.create_policy(policy, "validator")
        service.publish_policy(policy.policy_id, "validator")
        service.assign(
            RoleAssignment(
                policy_id=policy.policy_id,
                role_code="RISK_REVIEWER",
                actor_id="independent-reviewer",
                effective_from=now - timedelta(minutes=1),
                assigned_by="validator",
                rationale="Synthetic test",
                created_at=now,
                provenance=provenance,
            ),
            "validator",
        )
        case = DecisionCase(
            bid_id="B-SYNTH-15",
            case_code="RISK-1",
            decision_type=DecisionType.RESIDUAL_CONTRACT_RISK,
            title="Synthetic residual risk",
            owner="requestor",
            lifecycle_state=CaseLifecycle.ACTIVE,
            created_by="requestor",
            created_at=now,
            provenance=provenance,
        )
        service.create_case(case, "requestor")
        subject = SubjectLink(
            bid_id=case.bid_id,
            case_id=case.case_id,
            subject_type=SubjectType.CONTRACT_ISSUE,
            subject_id="ISSUE-SYNTH",
            relation="EVIDENCE",
            created_at=now,
            created_by="requestor",
        )
        service.add_subject(subject, "requestor")
        package = DecisionPackage(
            case_id=case.case_id,
            bid_id=case.bid_id,
            version_number=1,
            issue="Synthetic issue",
            options=("Accept", "Mitigate"),
            effects={"Accept": "Residual exposure", "Mitigate": "Lower exposure"},
            recommendation="Mitigate",
            requested_outcome="Route for independent review",
            residual_risk="Synthetic residual risk",
            deadline=now + timedelta(days=1),
            subject_links=(subject,),
            author="requestor",
            created_at=now,
        )
        service.add_package(package, "requestor")
        route = service.route(
            RouteCycle(
                case_id=case.case_id,
                bid_id=case.bid_id,
                package_id=package.package_id,
                policy_id=policy.policy_id,
                matched_rule_ids=(),
                requirements=(),
                requestor="requestor",
                submitted_at=now,
            ),
            {"decision_type": "RESIDUAL_CONTRACT_RISK"},
            actor="requestor",
        )
        assert route.requirements and package.fingerprint == package.model_copy().fingerprint
        event = ApprovalEvent(
            route_id=route.route_id,
            requirement_id=route.requirements[0].requirement_id,
            package_id=package.package_id,
            bid_id=case.bid_id,
            actor_id="independent-reviewer",
            decision=EventDecision.APPROVED,
            created_at=now,
        )
        service.event(event, "independent-reviewer")
        try:
            with db._conn() as conn:
                conn.execute(
                    "DELETE FROM decision_packages WHERE package_id=?", (package.package_id,)
                )
        except sqlite3.DatabaseError:
            pass
        else:
            raise AssertionError("immutable package deletion was permitted")
    print("TASK-15 validation: PASS")


if __name__ == "__main__":
    main()
