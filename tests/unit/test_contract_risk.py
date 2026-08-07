from datetime import UTC, datetime
from decimal import Decimal

import pytest

from core.contract_risk import (
    Consequence,
    ExposureBasis,
    Likelihood,
    ProposedDisposition,
    RiskAssessment,
    RiskCategory,
    RiskRating,
    risk_rating,
)
from core.enums import Actor
from core.schemas import Provenance


def test_neutral_matrix_is_deterministic() -> None:
    assert risk_rating(Likelihood.LIKELY, Consequence.MAJOR) == (12, RiskRating.HIGH)
    assert risk_rating(Likelihood.UNASSESSED, Consequence.SEVERE) == (None, RiskRating.UNASSESSED)


def test_exact_exposure_requires_order_and_currency() -> None:
    now = datetime.now(UTC)
    provenance = Provenance(created_by=Actor.HUMAN, agent_name="test")
    value = RiskAssessment(
        issue_id="CRI-1",
        bid_id="B-1",
        version_number=1,
        category=RiskCategory.WARRANTY,
        customer_position="Customer",
        company_position="Proposed",
        business_impact="Impact",
        affected_functions=("COMMERCIAL",),
        disposition=ProposedDisposition.PROPOSE_DEVIATION,
        likelihood=Likelihood.POSSIBLE,
        consequence=Consequence.MODERATE,
        exposure_basis=ExposureBasis.MONETARY_RANGE,
        minimum=Decimal("1.000"),
        most_likely=Decimal("2.000"),
        maximum=Decimal("3.000"),
        currency="CAD",
        assessed_by="author",
        assessed_at=now,
        provenance=provenance,
        created_at=now,
    )
    assert str(value.maximum) == "3.000"
    with pytest.raises(ValueError):
        value.model_copy(update={"currency": None}).__class__.model_validate(
            {**value.model_dump(), "currency": None}
        )
