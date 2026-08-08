"""Synthetic deterministic TASK-16 calculation and baseline acceptance oracle."""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from core.commercial_scenarios import (
    BaselineSelection,
    CashDirection,
    CashEvent,
    ReviewDecision,
    ScenarioAssumption,
    ScenarioFamily,
    ScenarioLine,
    ScenarioPurpose,
    ScenarioReview,
    ScenarioSourceLink,
    ScenarioVersion,
    calculate_scenario,
)
from core.database import Database
from core.scenario_repository import SCENARIO_MIGRATION_ID, ScenarioRepository
from core.scenario_service import ScenarioService


def main() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    with tempfile.TemporaryDirectory(prefix="contractiq-task16-") as root:
        db = Database(Path(root) / "scenarios.db")
        repository = ScenarioRepository(db)
        service = ScenarioService(repository)
        with db._conn() as conn:
            assert conn.execute(
                "SELECT 1 FROM scenario_schema_migrations WHERE migration_id=?",
                (SCENARIO_MIGRATION_ID,),
            ).fetchone()
        family = ScenarioFamily(
            bid_id="B-SYNTH-16",
            code="BASE-SYNTH",
            purpose=ScenarioPurpose.BASE,
            title="Synthetic base",
            owner="synthetic-owner",
            intent="Synthetic calculation",
            created_by="synthetic-author",
            created_at=now,
        )
        service.create_family(family, "synthetic-author")
        source = ScenarioSourceLink(
            bid_id=family.bid_id,
            scenario_version_id="pending",
            source_type="TASK_13_COMMERCIAL_ASSESSMENT",
            source_id="COMM-SYNTH",
            source_version_id="ASSESS-SYNTH",
            exact_amount=Decimal("100.00"),
            currency="CAD",
        )
        version = ScenarioVersion(
            family_id=family.family_id,
            bid_id=family.bid_id,
            version_number=1,
            presentation_currency="CAD",
            lines=(
                ScenarioLine(
                    role="CUSTOMER_REVENUE",
                    amount=Decimal("100.00"),
                    currency="CAD",
                    contributes_to_revenue=True,
                ),
                ScenarioLine(
                    role="DIRECT_PRODUCT_COST",
                    amount=Decimal("60.00"),
                    currency="CAD",
                    contributes_to_cost=True,
                ),
            ),
            assumptions=(
                ScenarioAssumption(
                    assumption_type="FX_RATE",
                    value=Decimal("1.2500"),
                    unit="CAD/USD",
                    effective_date=date(2026, 1, 2),
                    rationale="Synthetic explicit rate",
                ),
            ),
            cash_events=(
                CashEvent(
                    event_date=date(2026, 1, 2),
                    direction=CashDirection.INFLOW,
                    amount=Decimal("100.00"),
                    currency="CAD",
                    event_type="CUSTOMER_PAYMENT",
                    rationale="Synthetic",
                ),
                CashEvent(
                    event_date=date(2026, 1, 3),
                    direction=CashDirection.OUTFLOW,
                    amount=Decimal("60.00"),
                    currency="CAD",
                    event_type="SUPPLIER_PAYMENT",
                    rationale="Synthetic",
                ),
            ),
            source_links=(source.model_copy(update={"scenario_version_id": "pending"}),),
            created_by="synthetic-author",
            created_at=now,
        )
        # Source links are immutable snapshots; bind the exact version identity before persistence.
        version = version.model_copy(
            update={
                "source_links": (
                    source.model_copy(update={"scenario_version_id": version.scenario_version_id}),
                )
            }
        )
        result = service.calculate(version, "synthetic-author")
        assert result.revenue == Decimal("100.00") and result.total_cost == Decimal("60.00")
        assert result.gross_profit == Decimal("40.00") and result.gross_margin_bps == 4000
        assert calculate_scenario(version).fingerprint == result.fingerprint
        try:
            service.review(
                ScenarioReview(
                    scenario_version_id=version.scenario_version_id,
                    decision=ReviewDecision.ACCEPTED,
                    reviewer="synthetic-author",
                    reviewed_at=now,
                    rationale="self",
                ),
                family.bid_id,
                "synthetic-author",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("self-review was accepted")
        service.review(
            ScenarioReview(
                scenario_version_id=version.scenario_version_id,
                decision=ReviewDecision.ACCEPTED,
                reviewer="synthetic-reviewer",
                reviewed_at=now,
                rationale="Synthetic data-quality acceptance",
            ),
            family.bid_id,
            "synthetic-operator",
        )
        service.select_baseline(
            BaselineSelection(
                bid_id=family.bid_id,
                scenario_version_id=version.scenario_version_id,
                selected_by="synthetic-selector",
                selected_at=now,
                rationale="Explicit synthetic selection",
            )
        )
        try:
            with db._conn() as conn:
                conn.execute(
                    "DELETE FROM scenario_versions WHERE scenario_version_id=?",
                    (version.scenario_version_id,),
                )
        except sqlite3.DatabaseError:
            pass
        else:
            raise AssertionError("immutable scenario deletion was permitted")
    print("TASK-16 validation: PASS")


if __name__ == "__main__":
    main()
