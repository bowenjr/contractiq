"""Synthetic deterministic TASK-17 negotiation acceptance oracle."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from core.database import Database
from core.negotiation import (
    Applicability,
    Concession,
    ConditionalTrade,
    Mandate,
    MovementType,
    NegotiationIssue,
    NegotiationMovement,
    NegotiationPlan,
    PlanLifecycle,
    PlanVersion,
    Priority,
    TradeState,
    validate_concession,
)
from core.negotiation_repository import NEGOTIATION_MIGRATION_ID, NegotiationRepository
from core.negotiation_service import NegotiationService


def main() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    with tempfile.TemporaryDirectory(prefix="contractiq-task17-") as root:
        db = Database(Path(root) / "negotiation.db")
        repository = NegotiationRepository(db)
        service = NegotiationService(repository)
        with db._conn() as conn:
            assert conn.execute(
                "SELECT 1 FROM negotiation_schema_migrations WHERE migration_id=?",
                (NEGOTIATION_MIGRATION_ID,),
            ).fetchone()
        plan = NegotiationPlan(
            bid_id="B-SYNTH-17",
            code="PLAN-SYNTH",
            applicability=Applicability.NEGOTIATION_REQUIRED,
            title="Synthetic negotiation",
            owner="synthetic-owner",
            lifecycle=PlanLifecycle.ACTIVE,
            created_by="synthetic",
            created_at=now,
        )
        service.create_plan(plan, "synthetic")
        issue = NegotiationIssue(
            plan_id=plan.plan_id,
            code="PRICE",
            priority=Priority.MUST_CHANGE,
            owner="synthetic-owner",
            customer_current="100",
            opening="110",
            target="105",
            fallback_minimum="100",
            walk_away_or_escalate="Escalate",
            rationale="Synthetic",
        )
        version = PlanVersion(
            plan_id=plan.plan_id,
            bid_id=plan.bid_id,
            version_number=1,
            issues=(issue,),
            created_by="synthetic",
            created_at=now,
        )
        service.add_version(version, "synthetic")
        mandate = Mandate(
            plan_version_id=version.plan_version_id,
            bid_id=plan.bid_id,
            authorized_actors=("synthetic-negotiator",),
            allowed_actions=("OFFER",),
            issue_codes=("PRICE",),
            limit_amount=Decimal("10"),
            currency="CAD",
            starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(hours=1),
            state="AUTHORIZED",
        )
        service.add_mandate(mandate, "synthetic")
        trade = ConditionalTrade(
            bid_id=plan.bid_id,
            plan_version_id=version.plan_version_id,
            give="payment extension",
            get="price protection",
            required_value="signed confirmation",
            state=TradeState.PLANNED,
            created_at=now,
        )
        service.add_trade(trade, "synthetic")
        try:
            service.add_trade(trade.model_copy(update={"state": TradeState.COMMITTED}), "synthetic")
        except ValueError:
            pass
        else:
            raise AssertionError("un-evidenced conditional trade committed")
        concession = Concession(
            bid_id=plan.bid_id,
            issue_code="PRICE",
            version_number=1,
            amount=Decimal("5"),
            currency="CAD",
            unit="TOTAL",
            basis="SYNTHETIC",
            mandate_id=mandate.mandate_id,
            created_at=now,
        )
        validate_concession(concession, mandate, "synthetic-negotiator", now)
        movement = NegotiationMovement(
            bid_id=plan.bid_id,
            movement_type=MovementType.COMPANY_COMMITMENT_RECORDED,
            issue_code="PRICE",
            actor="synthetic-negotiator",
            text="Synthetic commitment",
            authority_id=mandate.mandate_id,
            created_at=now,
        )
        service.add_movement(movement)
        try:
            with db._conn() as conn:
                conn.execute(
                    "DELETE FROM negotiation_movements WHERE event_id=?", (movement.event_id,)
                )
        except sqlite3.DatabaseError:
            pass
        else:
            raise AssertionError("movement deletion was permitted")
    print("TASK-17 validation: PASS")


if __name__ == "__main__":
    main()
