"""TASK-17 negotiation workflow boundary."""

from __future__ import annotations

from core.negotiation import (
    Concession,
    ConditionalTrade,
    Mandate,
    NegotiationMovement,
    NegotiationPlan,
    PlanVersion,
    validate_concession,
)
from core.negotiation_repository import NegotiationRepository


class NegotiationService:
    def __init__(self, repository: NegotiationRepository) -> None:
        self.repository = repository

    def create_plan(self, value: NegotiationPlan, actor: str = "operator") -> NegotiationPlan:
        self.repository.create_plan(value, actor)
        return value

    def add_version(self, value: PlanVersion, actor: str = "operator") -> PlanVersion:
        self.repository.add_version(value, actor)
        return value

    def add_mandate(self, value: Mandate, actor: str = "operator") -> Mandate:
        if value.state != "AUTHORIZED":
            raise ValueError("mandates must have explicit authorized state")
        self.repository.add_mandate(value, actor)
        return value

    def add_trade(self, value: ConditionalTrade, actor: str = "operator") -> ConditionalTrade:
        if value.state == "COMMITTED" and value.value_state != "EVIDENCED":
            raise ValueError("conditional give cannot commit before value is evidenced")
        self.repository.add_trade(value, actor)
        return value

    def add_movement(self, value: NegotiationMovement) -> NegotiationMovement:
        self.repository.add_movement(value)
        return value

    def add_concession(
        self, value: Concession, mandate: Mandate | None, actor: str, at
    ) -> Concession:
        validate_concession(value, mandate, actor, at)
        self.repository.add_concession(value, actor)
        return value

    def metrics(self, bid_id: str | None = None) -> dict[str, int]:
        return self.repository.metrics(bid_id)
