import pytest

from core.materiality import MATERIALITY_BY_CONDITION, is_material


@pytest.mark.parametrize("condition_id", sorted(MATERIALITY_BY_CONDITION))
def test_every_current_condition_is_material(condition_id: str) -> None:
    assert is_material(condition_id, "Any current-condition detail") is True


def test_unknown_conditions_fail_safe_to_material() -> None:
    assert is_material("future.unclassified_condition", "Unknown impact") is True
