"""Application service for TASK-13 commercial completeness workflows."""

from __future__ import annotations

from builtins import list as builtin_list
from datetime import date
from typing import Any

from core.commercial import AssessmentVersion, CommercialItem, CommercialLink, CommercialReview
from core.commercial_repository import CommercialRepository
from core.commercial_rules import calculate_commercial_gaps, commercial_metrics


class CommercialService:
    def __init__(self, repository: CommercialRepository) -> None:
        self.repository = repository

    def list(self, bid_id: str | None = None) -> builtin_list[dict[str, Any]]:
        return self.repository.list(bid_id)

    def create(self, item: CommercialItem, actor: str = "operator") -> CommercialItem:
        self.repository.create(item, actor)
        return item

    def initialize_standard(self, bid_id: str, actor: str = "operator") -> builtin_list[str]:
        return self.repository.initialize_standard(bid_id, actor)

    def add_link(self, link: CommercialLink, actor: str = "operator") -> CommercialLink:
        self.repository.add_link(link, actor)
        return link

    def activate(self, item_id: str, expected_version: int, actor: str = "operator") -> None:
        self.repository.activate(item_id, expected_version, actor)

    def add_assessment(
        self, value: AssessmentVersion, actor: str = "operator"
    ) -> AssessmentVersion:
        self.repository.add_assessment(value, actor)
        return value

    def review(self, value: CommercialReview, actor: str = "operator") -> CommercialReview:
        self.repository.add_review(value, actor)
        return value

    def detail(self, item_id: str) -> dict[str, Any]:
        rows = [row for row in self.repository.list() if row["commercial_item_id"] == item_id]
        if not rows:
            raise ValueError("commercial item not found")
        return {
            "item": rows[0],
            "links": self.repository.links(item_id),
            "assessments": self.repository.assessments(item_id),
            "reviews": self.repository.reviews(item_id),
        }

    def gaps(
        self,
        bid_id: str | None,
        as_of: date,
        scope_items: builtin_list[dict[str, Any]] | None = None,
    ) -> builtin_list[Any]:
        items = self.repository.list(bid_id)
        ids = [row["commercial_item_id"] for row in items]
        return calculate_commercial_gaps(
            items,
            as_of=as_of,
            links={key: self.repository.links(key) for key in ids},
            assessments={key: self.repository.assessments(key) for key in ids},
            reviews={key: self.repository.reviews(key) for key in ids},
            scope_items=scope_items,
            expected_bid_id=bid_id,
        )

    def metrics(
        self,
        bid_id: str | None,
        as_of: date,
        scope_items: builtin_list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        return commercial_metrics(
            self.repository.list(bid_id), self.gaps(bid_id, as_of, scope_items)
        )
