"""Application service for recording accountable owner pursuit decisions."""

from __future__ import annotations

from core.database import Database
from core.owner_decision_repository import OwnerDecisionRepository
from core.owner_decisions import OwnerDecision, OwnerDecisionValue


class OwnerDecisionService:
    """Validate owner input and delegate the atomic append to its repository."""

    def __init__(
        self,
        db: Database,
        repository: OwnerDecisionRepository | None = None,
    ) -> None:
        self.repository = repository or OwnerDecisionRepository(db)

    def record_owner_decision(
        self,
        bid_id: str,
        decision: OwnerDecisionValue | str,
        reason: str,
        actor: str,
        expected_current_decision_id: str | None = None,
    ) -> OwnerDecision:
        """Record one validated decision, status transition, and audit atomically."""
        try:
            parsed_decision = OwnerDecisionValue(decision)
        except ValueError as exc:
            raise ValueError(
                "Owner decision must be PURSUE or DO_NOT_PURSUE; choose one valid outcome"
            ) from exc
        normalized_reason = reason.strip()
        if not normalized_reason or len(normalized_reason) > 500:
            raise ValueError(
                "Owner decision reason must contain 1 to 500 characters; revise the reason"
            )
        normalized_actor = actor.strip()
        if not normalized_actor:
            raise ValueError("Owner decision actor is required; identify who made the decision")
        return self.repository.record(
            bid_id,
            parsed_decision,
            normalized_reason,
            normalized_actor,
            expected_current_decision_id,
        )


__all__ = ["OwnerDecisionService"]
