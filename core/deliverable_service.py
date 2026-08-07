"""Application service for TASK-12 deliverable workflows."""

from __future__ import annotations

from builtins import list as builtin_list
from datetime import date
from typing import Any

from core.deliverable_repository import DeliverableRepository
from core.deliverable_rules import calculate_deliverable_gaps, deliverable_metrics
from core.deliverables import (
    Deliverable,
    DeliverableLink,
    ReviewDecisionRecord,
    SubmissionVersion,
    SupplierCommitment,
)


class DeliverableService:
    def __init__(self, repository: DeliverableRepository) -> None:
        self.repository = repository

    def list(self, bid_id: str | None = None) -> builtin_list[dict[str, Any]]:
        return self.repository.list(bid_id)

    def create(self, item: Deliverable, actor: str = "operator") -> Deliverable:
        self.repository.create(item, actor)
        return item

    def add_link(self, link: DeliverableLink, actor: str = "operator") -> DeliverableLink:
        self.repository.add_link(link, actor)
        return link

    def activate(self, deliverable_id: str, expected_version: int, actor: str = "operator") -> None:
        self.repository.activate(deliverable_id, expected_version, actor)

    def add_commitment(
        self, commitment: SupplierCommitment, actor: str = "operator"
    ) -> SupplierCommitment:
        self.repository.add_commitment(commitment, actor)
        return commitment

    def submit(self, submission: SubmissionVersion, actor: str = "operator") -> SubmissionVersion:
        self.repository.add_submission(submission, actor)
        return submission

    def review(self, review: ReviewDecisionRecord, actor: str = "operator") -> ReviewDecisionRecord:
        self.repository.add_review(review, actor)
        return review

    def detail(self, deliverable_id: str) -> dict[str, Any]:
        rows = [row for row in self.repository.list() if row["deliverable_id"] == deliverable_id]
        if not rows:
            raise ValueError("deliverable not found")
        return {
            "item": rows[0],
            "links": self.repository.links(deliverable_id),
            "commitments": self.repository.commitments(deliverable_id),
            "submissions": self.repository.submissions(deliverable_id),
            "reviews": self.repository.reviews(deliverable_id),
        }

    def history(self, deliverable_id: str) -> builtin_list[dict[str, Any]]:
        return self.repository.history(deliverable_id)

    def gaps(self, bid_id: str | None, as_of: date) -> builtin_list[Any]:
        items = self.repository.list(bid_id)
        ids = [row["deliverable_id"] for row in items]
        return calculate_deliverable_gaps(
            items,
            as_of=as_of,
            links={key: self.repository.links(key) for key in ids},
            commitments={key: self.repository.commitments(key) for key in ids},
            submissions={key: self.repository.submissions(key) for key in ids},
            reviews={key: self.repository.reviews(key) for key in ids},
        )

    def metrics(self, bid_id: str | None, as_of: date) -> dict[str, int]:
        items = self.repository.list(bid_id)
        return deliverable_metrics(self.gaps(bid_id, as_of), items)
