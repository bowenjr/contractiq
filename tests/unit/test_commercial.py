from datetime import UTC, datetime
from decimal import Decimal

import pytest

from core.commercial import (
    Applicability,
    AssessmentVersion,
    CommercialTreatment,
    EvidenceBasis,
)
from core.commercial_rules import calculate_commercial_gaps, commercial_metrics
from core.enums import Actor
from core.schemas import Provenance


def test_decimal_round_trip_and_treatment_coherence() -> None:
    now = datetime.now(UTC)
    value = AssessmentVersion(
        commercial_item_id="COM-1",
        bid_id="B-1",
        version_number=1,
        applicability=Applicability.APPLICABLE,
        treatment=CommercialTreatment.FIRM_PRICED,
        amount=Decimal("123.450000"),
        currency="CAD",
        evidence_basis=EvidenceBasis.BOUNDED_MANUAL_DECISION,
        rationale="Synthetic basis",
        assessed_by="author",
        assessed_at=now,
        provenance=Provenance(created_by=Actor.HUMAN, agent_name="test"),
        created_at=now,
    )
    assert str(value.amount) == "123.450000"
    with pytest.raises(ValueError):
        AssessmentVersion(
            commercial_item_id="COM-1",
            bid_id="B-1",
            version_number=2,
            applicability=Applicability.APPLICABLE,
            treatment=CommercialTreatment.FIRM_PRICED,
            amount=Decimal("1"),
            assessed_by="author",
            assessed_at=now,
            provenance=Provenance(created_by=Actor.HUMAN, agent_name="test"),
            created_at=now,
        )


def test_scope_mismatch_and_zero_population_metrics_are_explicit() -> None:
    scope = [
        {
            "bid_id": "B-1",
            "scope_item_id": "S-1",
            "offer_position": "INCLUDED",
            "pricing_state": "ALLOWANCED",
        }
    ]
    gaps = calculate_commercial_gaps(
        [], as_of=datetime(2026, 1, 1, tzinfo=UTC).date(), scope_items=scope, expected_bid_id="B-1"
    )
    assert "COMMERCIAL_SCOPE_INCLUDED_NO_COVERAGE" in {gap.code for gap in gaps}
    metrics = commercial_metrics([], gaps)
    assert metrics["has_population"] == 0
    assert metrics["active_items"] == 0
