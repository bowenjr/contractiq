import pytest

from core.taxonomy import normalize_obligation_type, normalize_trigger


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("performance", "PERF"),
        ("delivery obligation", "PERF"),
        ("payment", "PAY"),
        ("Payment obligation", "PAY"),
        ("Financial", "PAY"),
        ("notice", "NOTC"),
        ("notification obligation", "NOTC"),
        ("approval", "APPR"),
        ("consent requirement", "APPR"),
        ("reporting", "RPT"),
        ("reporting requirement", "RPT"),
        ("insurance", "INS"),
        ("insurance requirement", "INS"),
        ("compliance", "COMP"),
        ("regulatory compliance", "COMP"),
        ("restrictive covenant", "REST"),
        ("condition precedent", "COND"),
        ("survival obligation", "SURV"),
    ],
)
def test_normalize_obligation_type_known_variants(raw: str, expected: str) -> None:
    assert normalize_obligation_type(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("failure to give notice", "negative"),
        ("failure to notify", "negative"),
        ("auto-renew", "negative"),
        ("auto-renewal", "negative"),
        ("date-based", "calendar"),
        ("recurring schedule", "calendar"),
        ("specific date", "calendar"),
        ("event-based", "event"),
        ("triggering event", "event"),
        ("upon receipt of invoice", "event"),
        ("condition-based", "condition"),
        ("if condition is met", "condition"),
        ("milestone-based", "milestone"),
        ("project milestone", "milestone"),
        ("within 10 days of acceptance", "milestone"),
        ("rolling period", "rolling"),
        ("recurring", "calendar"),
        ("within 30 days of the effective date", "rolling"),
        ("ongoing", "continuous"),
        ("at all times", "continuous"),
        ("throughout the term", "continuous"),
        ("deemed acceptance", "negative"),
        ("failure to object", "negative"),
    ],
)
def test_normalize_trigger_known_variants(raw: str, expected: str) -> None:
    assert normalize_trigger(raw) == expected


@pytest.mark.parametrize("canonical", ["PERF", "PAY", "NOTC", "INS", "SURV"])
def test_canonical_obligation_values_pass_through(canonical: str) -> None:
    assert normalize_obligation_type(canonical) == canonical


@pytest.mark.parametrize(
    "canonical",
    ["calendar", "event", "condition", "milestone", "rolling", "continuous", "negative"],
)
def test_canonical_trigger_values_pass_through(canonical: str) -> None:
    assert normalize_trigger(canonical) == canonical


def test_unrecognized_and_none_values_pass_through() -> None:
    assert normalize_obligation_type("bespoke legacy category") == "bespoke legacy category"
    assert normalize_trigger("after customer acceptance") == "after customer acceptance"
    assert normalize_obligation_type(None) is None
    assert normalize_trigger(None) is None
