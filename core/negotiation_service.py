"""TASK-17 negotiation workflow boundary."""

from __future__ import annotations

from datetime import datetime

from core.negotiation import (
    Concession,
    ConditionalTrade,
    Mandate,
    NegotiationMovement,
    NegotiationPlan,
    PlanVersion,
    TradeState,
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
        self, value: Concession, mandate: Mandate | None, actor: str, at: datetime
    ) -> Concession:
        validate_concession(value, mandate, actor, at)
        self.repository.add_concession(value, actor)
        return value

    def withdraw_plan(self, plan_id: str, actor: str, reason: str) -> None:
        self.repository.withdraw_plan(plan_id, actor, reason)

    def revoke_mandate(self, mandate_id: str, actor: str, reason: str) -> None:
        self.repository.revoke_mandate(mandate_id, actor, reason)

    def progress_trade(self, trade_id: str, new_state: TradeState, actor: str, reason: str) -> None:
        self.repository.progress_trade(trade_id, new_state, actor, reason)

    def current_trade(self, trade_id: str) -> ConditionalTrade:
        return self.repository.current_trade(trade_id)

    def metrics(self, bid_id: str | None = None) -> dict[str, int]:
        return self.repository.metrics(bid_id)
