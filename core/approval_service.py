"""TASK-15 approval workflow service."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from core.approval_authority import (
    ApprovalEvent,
    AuthorityPolicy,
    DecisionCase,
    DecisionPackage,
    RoleAssignment,
    RouteCycle,
    SubjectLink,
)
from core.approval_repository import ApprovalRepository
from core.approval_rules import approval_gaps, match_policy


class ApprovalService:
    def __init__(self, repository: ApprovalRepository) -> None:
        self.repository = repository

    def create_policy(self, v: AuthorityPolicy, actor: str = "operator") -> AuthorityPolicy:
        self.repository.create_policy(v, actor)
        return v

    def publish_policy(self, policy_id: str, actor: str = "operator") -> None:
        self.repository.publish_policy(policy_id, actor)

    def assign(self, v: RoleAssignment, actor: str = "operator") -> RoleAssignment:
        self.repository.assign(v, actor)
        return v

    def create_case(self, v: DecisionCase, actor: str = "operator") -> DecisionCase:
        self.repository.create_case(v, actor)
        return v

    def add_subject(self, v: SubjectLink, actor: str = "operator") -> SubjectLink:
        self.repository.add_subject(v, actor)
        return v

    def add_package(self, v: DecisionPackage, actor: str = "operator") -> DecisionPackage:
        self.repository.add_package(v, actor)
        return v

    def route(
        self,
        v: RouteCycle,
        policy_or_facts: AuthorityPolicy | dict[str, Any],
        facts: dict[str, Any] | None = None,
        actor: str = "operator",
    ) -> RouteCycle:
        if isinstance(policy_or_facts, AuthorityPolicy):
            policy = policy_or_facts
            match_facts = facts or {}
        else:
            policies = [p for p in self.repository.policy_models() if p.policy_id == v.policy_id]
            if not policies:
                raise ValueError("policy not found")
            policy = policies[0]
            match_facts = policy_or_facts
        evaluation = match_policy(policy, match_facts)
        value = cast(
            RouteCycle,
            v.model_copy(
                update={
                    "matched_rule_ids": evaluation.matched_rule_ids,
                    "requirements": tuple(
                        req.model_copy(update={"route_id": v.route_id})
                        for req in evaluation.requirements
                    ),
                }
            ),
        )
        self.repository.create_route(value, actor)
        return value

    def event(self, v: ApprovalEvent, actor: str = "operator") -> ApprovalEvent:
        self.repository.event(v, actor)
        return v

    def gaps(
        self, bid_id: str | None = None, as_of: datetime | None = None
    ) -> list[dict[str, str]]:
        return approval_gaps(
            self.repository.cases(bid_id),
            self.repository.routes(bid_id),
            self.repository.policies(),
            as_of=as_of or datetime.now(),
        )
