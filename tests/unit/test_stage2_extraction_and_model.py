"""Stage 2 — VDDR table reading, the classified model contract, guards and consolidation.

All text is synthetic; nothing here calls a network or contains customer content.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Any

import pytest

from core.deliverable_discovery import ExtractionStatus, ExtractionUnit, LocatorType
from core.deliverable_discovery_extraction import VDDR_TABLE_KIND, RawUnit, extract_units
from core.deliverable_discovery_llm import (
    CANDIDATE_RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    analyse_units,
)
from core.deliverable_discovery_service import _consolidate, split_known_duplicates
from core.requirement_lifecycle import DiscoveryLifecycle
from core.vddr_table import (
    expand_header,
    find_vddr_header,
    group_lines,
    interpret_row_line,
    interpret_vddr_unit,
    render_rows,
)

NOW = datetime(2026, 10, 9, tzinfo=UTC)
HEADER = [
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
ROWS: list[list[Any]] = [
    ["SCHEDULE OF VENDOR DRAWING AND DATA REQUIREMENTS"] + [""] * 11,
    HEADER,
    ["Planning Y"] + [""] * 11,
    ["1.01", "GX", "Quality Plan", "Y", "1", "1", "2W ARO", "", "", "", "INFO ONLY", ""],
    ["1.02", "", "Site Safety Plan", "", "", "", "", "", "", "", "", ""],
    ["1.03", "", "Weekly Report", "Y", "", "", "", "", "", "", "", "Update\nweekly | by mail"],
    ["1.04", "EE", "Test Procedures", "Y", "", "1", "4W ARO", "3", "8W ARO", "1", "CERTIFIED", ""],
    ["1.05", "EE", "Unscheduled Item", "Y", "", "", "", "", "", "", "", ""],
]


def _unit(text: str, kind: str = "TEXT", document: str = "Part 1/Instructions.pdf") -> RawUnit:
    return RawUnit(
        locator_type="PAGE", locator="p.2", text=text, readable=True, document=document, kind=kind
    )


def _requirement(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "title": "Form of Bid",
        "description": "Signed bid form.",
        "lifecycle": "BID_RETURNABLE",
        "requirement_status": "REQUIRED",
        "item_kind": "SUBMISSION_DOCUMENT",
        "category": "Form",
        "condition_text": None,
        "applies_at": None,
        "responsible_party": "Bidder",
        "timing_text": None,
        "due_date": None,
        "dependencies": None,
        "confidence": "HIGH",
        "review_reason": None,
        "source_section": None,
        "source_quote": "The Bidder shall submit a signed Form of Bid with the proposal.",
    }
    item.update(overrides)
    return item


PASSAGE = (
    "The Bidder shall submit a signed Form of Bid with the proposal. "
    "Questions are due 3 Business Days Before RFP Closing Date."
)


class _Script:
    def __init__(self, reply: dict[str, object]) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, **_: object) -> dict[str, object]:
        self.prompts.append(prompt)
        return self.reply


class _Forbidden:
    def complete_json(self, *_: object, **__: object) -> dict[str, object]:
        raise AssertionError("the model must not be called for table rows")


# -- table reading ----------------------------------------------------------------------


def test_header_found_and_expanded() -> None:
    index = find_vddr_header(ROWS)
    assert index == 1
    names = expand_header(ROWS[index])
    assert "REVIEW QTY" in names and "REVIEW DUE" in names
    assert "FINAL QTY" in names and "FINAL DUE" in names
    assert find_vddr_header([["A", "B"], ["1", "2"]]) is None


def test_rows_render_as_self_contained_lines_and_skip_section_rows() -> None:
    lines = render_rows(ROWS, 1)
    assert len(lines) == 5
    assert lines[0].startswith("ROW 1.01 | DISC=GX | DESCRIPTION=Quality Plan | Required?=Y")
    assert "TIMING=Review 2W ARO" in lines[0]
    assert "|" not in lines[2].split("COMMENTS=", 1)[1]  # a pipe in a comment cannot split the row
    assert all(not line.startswith("ROW Planning") for line in lines)


def test_group_lines_never_split_a_line() -> None:
    lines = ["ROW 1.01 | " + "a" * 60, "ROW 1.02 | " + "b" * 60, "ROW 1.03 | " + "c" * 200]
    groups = group_lines(lines, limit=150)
    assert [g[2].count("ROW") for g in groups] == [2, 1]
    assert groups[0][0] == "1.01" and groups[0][1] == "1.02"
    assert any(len(g[2]) > 150 for g in group_lines(["ROW 9.01 | " + "x" * 300], limit=150))


def test_interpretation_separates_stages_and_marks_template_rows() -> None:
    findings = interpret_vddr_unit("\n".join(render_rows(ROWS, 1)))
    by_code: dict[str, list[str]] = {}
    for finding in findings:
        code = finding["source_section"].rsplit("item ", 1)[1]
        by_code.setdefault(code, []).append(finding["lifecycle"])
    assert by_code["1.01"] == ["BID_RETURNABLE", "POST_AWARD"]  # both components, distinct
    assert by_code["1.02"] == ["NOT_APPLICABLE"]  # Required? blank
    assert by_code["1.03"] == ["NEEDS_REVIEW"]  # Required, no stage given
    assert by_code["1.04"] == ["POST_AWARD"]  # review/final only: no tender component
    post = next(f for f in findings if f["title"] == "Test Procedures")
    assert post["timing_text"] == "Review 4W ARO; Final 8W ARO"
    assert post["due_date"] is None and post["responsible_party"] is None
    assert post["source_quote"].startswith("ROW 1.04")


def test_interpreting_a_non_row_line_is_refused_with_guidance() -> None:
    with pytest.raises(ValueError, match="Re-run discovery"):
        interpret_row_line("something else")


def test_pdf_table_failure_falls_back_to_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Bidder shall submit the signed bid form with the proposal today.")
    data = document.tobytes()

    def boom(self: object, *args: object, **kwargs: object) -> None:
        raise RuntimeError("table detection failed")

    monkeypatch.setattr(pymupdf.Page, "find_tables", boom)
    units = extract_units("plain.pdf", None, io.BytesIO(data))
    assert [u.kind for u in units] == ["TEXT"] and "signed bid form" in units[0].text


def test_vddr_unit_is_read_by_rules_with_no_model_call() -> None:
    text = "\n".join(render_rows(ROWS, 1))
    result = analyse_units(_Forbidden(), [("k1", _unit(text, VDDR_TABLE_KIND))])
    lifecycles = [c.classification.lifecycle for c in result.candidates if c.classification]
    assert lifecycles.count(DiscoveryLifecycle.BID_RETURNABLE) == 1
    assert result.outcomes[0].outcome.value == "ANALYSED"
    assert all(c.classification.classified_by.value == "RULE" for c in result.candidates)


# -- model contract and guards ----------------------------------------------------------


def test_prompt_carries_document_and_whole_passage_and_schema_is_strict() -> None:
    llm = _Script({"requirements": [_requirement()]})
    analyse_units(llm, [("k1", _unit(PASSAGE))])
    assert llm.prompts[0].startswith("Document: Part 1/Instructions.pdf\n")
    assert PASSAGE in llm.prompts[0]
    assert "Never invent" in SYSTEM_PROMPT
    items = CANDIDATE_RESPONSE_SCHEMA["properties"]["requirements"]["items"]  # type: ignore[index]
    assert set(items["required"]) == set(items["properties"])
    assert items["additionalProperties"] is False


def test_classified_reply_keeps_lifecycle_and_verified_quote() -> None:
    result = analyse_units(_Script({"requirements": [_requirement()]}), [("k1", _unit(PASSAGE))])
    candidate = result.candidates[0]
    assert candidate.classification is not None
    assert candidate.classification.lifecycle is DiscoveryLifecycle.BID_RETURNABLE
    assert candidate.classification.responsible_party == "Bidder"  # named in the passage
    assert candidate.criticality.value == "REQUIRED"


@pytest.mark.parametrize(
    ("override", "check"),
    [
        # G1 unverifiable quote -> kept, but sent to review with the model's view retained
        (
            {"source_quote": "A sentence that is nowhere in the passage at all."},
            lambda c: (
                c.lifecycle is DiscoveryLifecycle.NEEDS_REVIEW
                and c.model_lifecycle is DiscoveryLifecycle.BID_RETURNABLE
                and c.confidence.value == "LOW"
            ),
        ),
        # G3 responsible party not in the passage is dropped
        ({"responsible_party": "Procurement Manager"}, lambda c: c.responsible_party is None),
        # G4 applies_at is only kept for CONDITIONAL
        ({"applies_at": "POST_AWARD"}, lambda c: c.applies_at is None),
        # G4 conditional without a condition is not trusted
        (
            {"lifecycle": "CONDITIONAL", "applies_at": "WITH_BID", "condition_text": None},
            lambda c: c.lifecycle is DiscoveryLifecycle.NEEDS_REVIEW,
        ),
        # G5 low confidence goes to review
        (
            {"confidence": "LOW", "review_reason": "unsure"},
            lambda c: (
                c.lifecycle is DiscoveryLifecycle.NEEDS_REVIEW and c.review_reason == "unsure"
            ),
        ),
        # G6 commercial topic comes from the category when it is a known key
        (
            {"item_kind": "COMMERCIAL_QUALIFICATION", "category": "Insurance"},
            lambda c: c.commercial_topic == "INSURANCE",
        ),
        (
            {"item_kind": "COMMERCIAL_QUALIFICATION", "category": "Something else"},
            lambda c: c.commercial_topic == "QUALIFICATIONS",
        ),
    ],
)
def test_guards_downgrade_but_never_drop(override: dict[str, Any], check: Any) -> None:
    result = analyse_units(
        _Script({"requirements": [_requirement(**override)]}), [("k1", _unit(PASSAGE))]
    )
    assert len(result.candidates) == 1  # retained
    assert check(result.candidates[0].classification)


def test_timing_text_is_kept_only_when_it_is_in_the_passage() -> None:
    llm = _Script(
        {
            "requirements": [
                _requirement(timing_text="3 Business Days Before RFP Closing Date"),
                _requirement(title="Other", timing_text="Promptly after the weekend"),
            ]
        }
    )
    candidates = analyse_units(llm, [("k1", _unit(PASSAGE))]).candidates
    units = {"k1": _extraction("k1", PASSAGE)}
    proposals = _consolidate("B-1", "RUN-1", NOW, list(candidates), units)
    texts = {p.title: p.due_date_text for p in proposals}
    assert texts["Form of Bid"] == "3 Business Days Before RFP Closing Date"
    assert texts["Other"] is None  # invented timing is never recorded
    assert all(p.due_date is None for p in proposals)  # and no date is ever made up


def test_legacy_shaped_reply_still_works_and_is_unclassified() -> None:
    legacy = {
        "candidates": [
            {
                "title": "Signed Bid Form",
                "category": "FORM",
                "criticality": "REQUIRED",
                "condition_text": None,
                "due_date": None,
                "requested_format": None,
                "requested_recipient": None,
                "proposed_owner": None,
            }
        ]
    }
    result = analyse_units(_Script(legacy), [("k1", _unit(PASSAGE))])
    assert result.candidates[0].classification is None
    assert _Script({"candidates": "bad"}).reply  # malformed shapes stay invalid below
    bad = analyse_units(_Script({"candidates": "bad"}), [("k1", _unit(PASSAGE))])
    assert bad.outcomes[0].outcome.value == "INVALID_OUTPUT"


def test_unusable_item_is_counted_not_silently_accepted() -> None:
    reply = {"requirements": [_requirement(lifecycle="SOMETIMES"), _requirement()]}
    result = analyse_units(_Script(reply), [("k1", _unit(PASSAGE))])
    assert len(result.candidates) == 1 and result.invalid_count == 1


# -- consolidation and duplicates ---------------------------------------------------------


def _extraction(key: str, text: str, locator: str = "p.2", file_id: str = "F1") -> ExtractionUnit:
    return ExtractionUnit(
        extraction_id=key,
        bid_id="B-1",
        run_id="RUN-1",
        received_file_id=file_id,
        locator_type=LocatorType.PAGE,
        locator=locator,
        text_excerpt=text,
        char_count=len(text),
        status=ExtractionStatus.OK,
        created_at=NOW,
    )


def test_two_stages_of_one_obligation_stay_two_findings_and_documents_merge() -> None:
    tender = _requirement(title="Quality Plan")
    after = _requirement(title="Quality Plan", lifecycle="POST_AWARD")
    llm_a = _Script({"requirements": [tender, after]})
    llm_b = _Script({"requirements": [tender]})
    cand = list(analyse_units(llm_a, [("k1", _unit(PASSAGE))]).candidates)
    cand += [
        c.model_copy(update={"extraction_id": "k2"})
        for c in analyse_units(llm_b, [("k2", _unit(PASSAGE))]).candidates
    ]
    lookup = {
        "k1": _extraction("k1", PASSAGE),
        "k2": _extraction("k2", PASSAGE, locator="p.9", file_id="F2"),
    }
    proposals = _consolidate("B-1", "RUN-1", NOW, cand, lookup)
    by_phase = {p.classification.lifecycle: p for p in proposals if p.classification}
    assert set(by_phase) == {DiscoveryLifecycle.BID_RETURNABLE, DiscoveryLifecycle.POST_AWARD}
    assert len(by_phase[DiscoveryLifecycle.BID_RETURNABLE].sources) == 2  # both documents cited
    assert len(by_phase[DiscoveryLifecycle.POST_AWARD].sources) == 1


def test_refinding_adds_sources_to_the_existing_finding_instead_of_a_duplicate() -> None:
    cand = analyse_units(_Script({"requirements": [_requirement()]}), [("k1", _unit(PASSAGE))])
    proposals = _consolidate(
        "B-1", "RUN-2", NOW, list(cand.candidates), {"k1": _extraction("k1", PASSAGE)}
    )
    key = proposals[0].dedup_key
    new, appends = split_known_duplicates(proposals, {key: "DDP-existing"})
    assert new == [] and appends[0][0] == "DDP-existing" and len(appends[0][1]) == 1
    # a finding recorded before classification (title+category key) also counts for a bid returnable
    legacy_key = "B-1:form:form-of-bid"
    new, appends = split_known_duplicates(proposals, {legacy_key: "DDP-legacy"})
    assert new == [] and appends[0][0] == "DDP-legacy"
    # an unrelated finding is new
    assert split_known_duplicates(proposals, {"B-1:other": "DDP-x"})[0] == proposals


def test_quote_of_cell_values_matches_a_labelled_spreadsheet_passage() -> None:
    from core.date_integrity import locate_quote

    passage = "A78: Nearest technical representatives | B78: Provide State & Country\nA79: Next"
    span = locate_quote(passage, "Nearest technical representatives | Provide State & Country")
    assert span is not None
    assert "B78:" in passage[span[0] : span[1]]  # the span covers the label inside it
    assert locate_quote(passage, "Nearest technical representatives | Provide City") is None
    # plain text is unchanged: the exact sentence still matches, an altered one still does not
    text = "The Bidder shall submit a signed Form of Bid with the proposal."
    assert locate_quote(text, "the bidder shall submit a signed form of bid") is not None
    assert locate_quote(text, "the Bidder shall submit an unsigned Form of Bid") is None
