from datetime import date
from decimal import Decimal

import pytest

from core.commercial_scenarios import (
    CashDirection,
    CashEvent,
    ScenarioLine,
    ScenarioVersion,
    calculate_scenario,
)


def version() -> ScenarioVersion:
    return ScenarioVersion(
        family_id="F-1",
        bid_id="B-1",
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
                amount=Decimal("40.00"),
                currency="CAD",
                contributes_to_cost=True,
            ),
        ),
        source_links=(),
        created_by="synthetic",
        created_at=__import__("datetime").datetime(2026, 1, 1),
        cash_events=(
            CashEvent(
                event_date=date(2026, 1, 1),
                direction=CashDirection.INFLOW,
                amount=Decimal("100.00"),
                currency="CAD",
                event_type="CUSTOMER_PAYMENT",
                rationale="synthetic",
            ),
        ),
    )


def test_exact_decimal_result_and_fingerprint() -> None:
    value = version()
    first = calculate_scenario(value)
    second = calculate_scenario(value)
    assert first.revenue == Decimal("100.00")
    assert first.gross_profit == Decimal("60.00")
    assert first.gross_margin_bps == 6000
    assert first.fingerprint == second.fingerprint


def test_zero_revenue_does_not_divide() -> None:
    value = version().model_copy(
        update={
            "lines": (
                ScenarioLine(
                    role="DIRECT_PRODUCT_COST",
                    amount=Decimal("1"),
                    currency="CAD",
                    contributes_to_cost=True,
                ),
            )
        }
    )
    result = calculate_scenario(value)
    assert result.gross_margin_bps is None


def test_source_amount_requires_currency() -> None:
    with pytest.raises(ValueError):
        from core.commercial_scenarios import ScenarioSourceLink

        ScenarioSourceLink(
            bid_id="B",
            scenario_version_id="V",
            source_type="TASK_13",
            source_id="I",
            source_version_id="A",
            exact_amount=Decimal("1"),
        )
