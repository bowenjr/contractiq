from collections import defaultdict

import pytest

from core.pillars import ALL_PILLARS, Pillar


def test_all_pillars_contains_exactly_seven_members() -> None:
    assert len(ALL_PILLARS) == 7


@pytest.mark.parametrize("pillar", ALL_PILLARS, ids=lambda pillar: pillar.pillar_id)
def test_each_pillar_has_characterisation_content(pillar: Pillar) -> None:
    assert pillar.key_questions
    assert pillar.red_flag_patterns
    assert pillar.missing_protection_patterns


def test_pillar_weights_are_floats_in_valid_range() -> None:
    for pillar in ALL_PILLARS:
        for weight in pillar.weight_by_doc_type.values():
            assert isinstance(weight, float)
            assert 0 < weight <= 1


def test_weights_sum_to_one_for_each_document_type() -> None:
    totals: defaultdict[str, float] = defaultdict(float)
    for pillar in ALL_PILLARS:
        for document_type, weight in pillar.weight_by_doc_type.items():
            totals[document_type] += weight

    assert totals
    for document_type, total in totals.items():
        assert total == pytest.approx(1.0, abs=0.05), f"{document_type}: {total}"
