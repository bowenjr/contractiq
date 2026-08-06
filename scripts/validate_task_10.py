# ruff: noqa: E501, I001, F841
"""Deterministic, synthetic TASK-10 migration and rules smoke validation."""

from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from core.database import Database
from core.scope_gap_rules import calculate_coverage, evaluate_gaps
from core.scope_interfaces import (
    CustomerNeed,
    Materiality,
    OfferPosition,
    PricingState,
    ScopeArea,
    ScopeItem,
    ScopeOrigin,
)
from core.scope_repository import SCOPE_INTERFACE_MIGRATION_ID, ScopeInterfaceRepository
from core.schemas import Provenance


def main() -> None:
    with TemporaryDirectory(prefix="contractiq-task10-") as root:
        db = Database(Path(root) / "validation.db")
        repo = ScopeInterfaceRepository(db)
        ScopeInterfaceRepository(db)
        item = ScopeItem(
            bid_id="synthetic-bid",
            title="Synthetic scope",
            description="Synthetic validation row",
            scope_area=ScopeArea.CORE_PRODUCTS,
            origin=ScopeOrigin.INTERNAL,
            customer_need=CustomerNeed.REQUIRED,
            offer_position=OfferPosition.UNDECIDED,
            pricing_state=PricingState.UNCONFIRMED,
            materiality=Materiality.MATERIAL,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            provenance=Provenance.from_human("validation"),
            created_by="validation",
        )
        # The row is projected without touching a production bid or document.
        gaps = evaluate_gaps([item], [], as_of_date=date(2026, 1, 1))
        coverage = calculate_coverage([item], [], gaps)
        assert any(g.code == "REQUIRED_UNASSIGNED" for g in gaps)
        assert coverage.active_scope_items == 1
        print(
            f"TASK-10 validation: PASS ({SCOPE_INTERFACE_MIGRATION_ID}; {len(gaps)} deterministic gaps)"
        )


if __name__ == "__main__":
    main()
