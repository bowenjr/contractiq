from datetime import UTC, datetime, timedelta

import pytest

from core.approval_authority import (
    AuthorityPolicy,
    DecisionPackage,
    DecisionType,
    PolicyLifecycle,
    StageMode,
)
from core.approval_rules import match_policy
from core.enums import Actor
from core.schemas import Provenance


def policy() -> AuthorityPolicy:
    now = datetime.now(UTC)
    return AuthorityPolicy(
        name="Synthetic",
        description="Synthetic",
        scope="BID",
        effective_from=now - timedelta(minutes=1),
        lifecycle_state=PolicyLifecycle.PUBLISHED,
        roles=("REVIEWER",),
        rules=({"rule_id": "R1", "dimensions": {"decision_type": "RESIDUAL_CONTRACT_RISK"}},),
        stages=({"order": 1, "mode": StageMode.ALL_REQUIRED.value, "roles": ["REVIEWER"]},),
        created_by="test",
        created_at=now,
        provenance=Provenance(created_by=Actor.HUMAN, human_confirmed=True, confirmed_by="test"),
    )


def test_policy_match_is_deterministic() -> None:
    first = match_policy(policy(), {"decision_type": DecisionType.RESIDUAL_CONTRACT_RISK})
    second = match_policy(policy(), {"decision_type": DecisionType.RESIDUAL_CONTRACT_RISK})
    assert first.matched_rule_ids == second.matched_rule_ids == ("R1",)
    assert first.requirements[0].role_code == "REVIEWER"


def test_package_requires_two_options() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="at least two options"):
        DecisionPackage(
            case_id="CASE-1",
            bid_id="BID-1",
            version_number=1,
            issue="Issue",
            options=("Only",),
            effects={},
            recommendation="Only",
            requested_outcome="Proceed",
            residual_risk="Known",
            deadline=now,
            subject_links=(),
            author="author",
            created_at=now,
        )
