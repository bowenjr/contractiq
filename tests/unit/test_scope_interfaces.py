from datetime import UTC, date, datetime

import pytest

from core.schemas import Provenance
from core.scope_gap_rules import calculate_coverage, evaluate_gaps
from core.scope_interfaces import (
    CustomerNeed,
    Materiality,
    OfferPosition,
    ScopeArea,
    ScopeItem,
    ScopeOrigin,
)


def _item(**changes: object) -> ScopeItem:
    values: dict[str, object] = {
        "bid_id": "B-2026-0001",
        "title": "Synthetic scope",
        "description": "Synthetic description",
        "scope_area": ScopeArea.CORE_PRODUCTS,
        "origin": ScopeOrigin.INTERNAL,
        "materiality": Materiality.MATERIAL,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "provenance": Provenance.from_human("test"),
        "created_by": "test",
    }
    values.update(changes)
    return ScopeItem(**values)


def test_all_fifteen_scope_areas_are_distinct() -> None:
    assert len(list(ScopeArea)) == 15
    assert len({area.value for area in ScopeArea}) == 15


def test_required_and_included_unpriced_are_separate_gaps() -> None:
    item = _item(customer_need=CustomerNeed.REQUIRED, offer_position=OfferPosition.INCLUDED)
    codes = {gap.code for gap in evaluate_gaps([item], [], as_of_date=date(2026, 1, 1))}
    assert {"INCLUDED_UNPRICED", "REQUIRED_UNASSIGNED"} <= codes


def test_included_not_applicable_pricing_is_rejected() -> None:
    with pytest.raises(ValueError, match="not-applicable pricing"):
        _item(offer_position=OfferPosition.INCLUDED, pricing_state="NOT_APPLICABLE")


def test_empty_coverage_has_no_population() -> None:
    coverage = calculate_coverage([], [], [])
    assert coverage.ratios["customer_need_assessed"].has_population is False
    assert coverage.ratios["customer_need_assessed"].percentage_basis_points == 0
