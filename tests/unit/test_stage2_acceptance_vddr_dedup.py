"""Acceptance regressions: VDDR header spelling, very long rows, same-run citation duplicates."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from core.deliverable_discovery import ExtractionStatus, ExtractionUnit, LocatorType
from core.deliverable_discovery_extraction import RawUnit
from core.deliverable_discovery_llm import analyse_units
from core.deliverable_discovery_service import _consolidate
from core.vddr_table import (
    MAX_QUOTE_CHARS,
    expand_header,
    find_vddr_header,
    interpret_vddr_unit,
    render_rows,
)

NOW = datetime(2026, 10, 9, tzinfo=UTC)


def _rows(header: list[Any], comment: str = "") -> list[list[Any]]:
    return [
        header,
        ["1.01", "GX", "Quality Plan", "Y", "1", "1", "2W ARO", "", "", "", "INFO ONLY", ""],
        ["1.02", "EE", "Test Procedures", "Y", "", "1", "4W ARO", "3", "8W ARO", "1", "", comment],
        ["1.03", "", "Site Safety Plan", "", "", "", "", "", "", "", "", ""],
    ]


UPPER = [
    "ITEM",
    "DISC",
    "DESCRIPTION",
    "Required?",
    "TENDER\nQTY",
    "REVIEW\nQTY DUE",
    None,
    "FINAL\nQTY DUE",
    None,
    "AS BUILT\nQTY",
    "FINAL STATUS",
    "COMMENTS",
]


def _summary(header: list[Any]) -> list[tuple[str, str]]:
    rows = _rows(header)
    index = find_vddr_header(rows)
    assert index == 0
    findings = interpret_vddr_unit("\n".join(render_rows(rows, index)))
    return [(f["title"], f["lifecycle"]) for f in findings]


@pytest.mark.parametrize(
    "variant",
    [
        lambda h: [c.lower() if isinstance(c, str) else c for c in h],
        lambda h: [c.title() if isinstance(c, str) else c for c in h],
        lambda h: [c.swapcase() if isinstance(c, str) else c for c in h],
    ],
    ids=["lower", "title", "swapcase"],
)
def test_header_spelling_does_not_change_the_findings(variant: Any) -> None:
    expected = _summary(UPPER)
    assert ("Quality Plan", "BID_RETURNABLE") in expected
    assert ("Test Procedures", "POST_AWARD") in expected
    assert ("Site Safety Plan", "NOT_APPLICABLE") in expected
    assert _summary(variant(UPPER)) == expected


def test_separate_review_columns_in_any_case_keep_their_timing() -> None:
    header = ["ITEM", "DISC", "description", "REQUIRED?", "tender qty", "review qty", "Review Due"]
    rows = [header, ["2.01", "EE", "Datasheet", "Y", "", "1", "4W ARO"]]
    names = expand_header(header)
    assert {"DESCRIPTION", "Required?", "TENDER QTY", "REVIEW QTY", "REVIEW DUE"} <= set(names)
    findings = interpret_vddr_unit("\n".join(render_rows(rows, 0)))
    assert [(f["lifecycle"], f["timing_text"]) for f in findings] == [
        ("POST_AWARD", "Review 4W ARO")
    ]


def test_upper_case_header_renders_exactly_as_before() -> None:
    line = render_rows(_rows(UPPER), 0)[0]
    assert line.startswith("ROW 1.01 | DISC=GX | DESCRIPTION=Quality Plan | Required?=Y")
    assert "TENDER QTY=1" in line


def test_a_very_long_row_still_yields_its_findings_with_a_bounded_verified_quote() -> None:
    comment = "Supplier shall coordinate with the Owner on every revision. " * 45
    rows = _rows(UPPER, comment=comment)
    lines = render_rows(rows, 0)
    assert len(lines[1]) > 2400
    text = "\n".join(lines)
    findings = interpret_vddr_unit(text)
    assert [(f["title"], f["lifecycle"]) for f in findings] == [
        ("Quality Plan", "BID_RETURNABLE"),
        ("Quality Plan", "POST_AWARD"),
        ("Test Procedures", "POST_AWARD"),
        ("Site Safety Plan", "NOT_APPLICABLE"),
    ]
    long_quote = findings[2]["source_quote"]
    assert len(long_quote) <= MAX_QUOTE_CHARS < 2000
    assert long_quote in text and lines[1].startswith(long_quote)

    result = analyse_units(object(), [("E-1", _vddr_unit(text))])  # type: ignore[arg-type]
    assert len(result.candidates) == 4
    assert result.outcomes[0].invalid_candidate_count == 0
    assert all(c.source_quote and c.source_quote in text for c in result.candidates)


def _vddr_unit(text: str) -> RawUnit:
    return RawUnit(
        locator_type="PAGE",
        locator="p.4 rows 1.01-1.03",
        text=text,
        readable=True,
        document="Appendix K.pdf",
        kind="VDDR_TABLE",
    )


# -- same-run citation duplicates ----------------------------------------------------------

PASSAGE = "The Bidder shall submit a signed Form of Bid with the proposal."


class _Script:
    def __init__(self, reply: dict[str, object]) -> None:
        self.reply = reply

    def complete_json(self, *_: object, **__: object) -> dict[str, object]:
        return self.reply


def _requirement() -> dict[str, Any]:
    return {
        "title": "Form of Bid",
        "description": "Signed bid form.",
        "lifecycle": "BID_RETURNABLE",
        "requirement_status": "REQUIRED",
        "item_kind": "SUBMISSION_DOCUMENT",
        "category": "Form",
        "condition_text": None,
        "applies_at": None,
        "responsible_party": None,
        "timing_text": None,
        "due_date": None,
        "dependencies": None,
        "confidence": "HIGH",
        "review_reason": None,
        "source_section": None,
        "source_quote": PASSAGE,
    }


def _extraction(key: str, locator: str, file_id: str = "F1") -> ExtractionUnit:
    return ExtractionUnit(
        extraction_id=key,
        bid_id="B-1",
        run_id="RUN-1",
        received_file_id=file_id,
        locator_type=LocatorType.PAGE,
        locator=locator,
        text_excerpt=PASSAGE,
        char_count=len(PASSAGE),
        status=ExtractionStatus.OK,
        created_at=NOW,
    )


def _consolidated(items: int, locators: list[tuple[str, str]]) -> list[Any]:
    reply = {"requirements": [_requirement() for _ in range(items)]}
    unit = RawUnit(locator_type="PAGE", locator="p.2", text=PASSAGE, readable=True)
    candidates = list(analyse_units(_Script(reply), [("k1", unit)]).candidates)
    lookup = {"k1": _extraction("k1", locators[0][1], locators[0][0])}
    for index, (file_id, locator) in enumerate(locators[1:], start=2):
        key = f"k{index}"
        candidates += [c.model_copy(update={"extraction_id": key}) for c in candidates[:1]]
        lookup[key] = _extraction(key, locator, file_id)
    return _consolidate("B-1", "RUN-1", NOW, candidates, lookup)


def test_the_same_obligation_returned_twice_from_one_passage_has_one_citation() -> None:
    proposals = _consolidated(2, [("F1", "p.2")])
    assert len(proposals) == 1
    assert len(proposals[0].sources) == 1


def test_different_locators_keep_their_own_citations() -> None:
    proposals = _consolidated(1, [("F1", "p.2"), ("F1", "p.9"), ("F2", "p.2")])
    assert len(proposals) == 1
    assert [(s.received_file_id, s.locator) for s in proposals[0].sources] == [
        ("F1", "p.2"),
        ("F1", "p.9"),
        ("F2", "p.2"),
    ]
