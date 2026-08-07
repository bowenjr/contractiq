"""Application service for contract-risk control."""

from __future__ import annotations

from builtins import list as builtin_list
from datetime import date
from typing import Any

from core.contract_risk import ContractIssue, RiskAssessment, RiskLink, RiskReview, RiskSource
from core.contract_risk_repository import ContractRiskRepository
from core.contract_risk_rules import calculate_risk_gaps, risk_metrics


class ContractRiskService:
    def __init__(self, repository: ContractRiskRepository) -> None:
        self.repository = repository

    def list(self, bid_id: str | None = None) -> builtin_list[dict[str, Any]]:
        return self.repository.list(bid_id)

    def create(self, v: ContractIssue, actor: str = "operator") -> ContractIssue:
        self.repository.create(v, actor)
        return v

    def add_source(self, v: RiskSource, actor: str = "operator") -> RiskSource:
        self.repository.add_source(v, actor)
        return v

    def add_link(self, v: RiskLink, actor: str = "operator") -> RiskLink:
        self.repository.add_link(v, actor)
        return v

    def activate(self, issue_id: str, expected: int, actor: str = "operator") -> None:
        self.repository.activate(issue_id, expected, actor)

    def assessment(self, v: RiskAssessment, actor: str = "operator") -> RiskAssessment:
        self.repository.add_assessment(v, actor)
        return v

    def review(self, v: RiskReview, actor: str = "operator") -> RiskReview:
        self.repository.add_review(v, actor)
        return v

    def detail(self, issue_id: str) -> dict[str, Any]:
        rows = [row for row in self.repository.list() if row["issue_id"] == issue_id]
        if not rows:
            raise ValueError("contract issue not found")
        return {
            "issue": rows[0],
            "sources": self.repository.sources(issue_id),
            "links": self.repository.links(issue_id),
            "assessments": self.repository.assessments(issue_id),
            "reviews": self.repository.reviews(issue_id),
        }

    def gaps(self, bid_id: str | None, as_of: date) -> builtin_list[Any]:
        items = self.repository.list(bid_id)
        ids = [row["issue_id"] for row in items]
        return calculate_risk_gaps(
            items,
            as_of=as_of,
            sources={key: self.repository.sources(key) for key in ids},
            assessments={key: self.repository.assessments(key) for key in ids},
            reviews={key: self.repository.reviews(key) for key in ids},
            links={key: self.repository.links(key) for key in ids},
        )

    def metrics(self, bid_id: str | None, as_of: date) -> dict[str, int]:
        return risk_metrics(self.repository.list(bid_id), self.gaps(bid_id, as_of))
