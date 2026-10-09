"""Stage 1 / Fix 4: long text is segmented into bounded, overlapping parts — never truncated."""

import io
import re

import pytest

from core.deliverable_discovery_extraction import (
    MAX_EXCERPT_CHARS,
    SEGMENT_OVERLAP_CHARS,
    extract_units,
    segment_spans,
    segment_text,
)
from tests.unit.test_deliverable_discovery_extraction import (
    _docx_bytes,
    _pdf_bytes,
    _xlsx_bytes,
)


def _words(count: int) -> str:
    """Numbered words, so any dropped or duplicated stretch is detectable."""
    return " ".join(f"w{index:05d}" for index in range(count))


def _sentences(count: int) -> str:
    return " ".join(
        f"Clause {index:04d} requires the Supplier to submit document {index}."
        for index in range(count)
    )


def _dense_pdf(sentences: int) -> bytes:
    """One PDF page holding many short lines, so all of the text is on the page."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    lines = [
        " ".join(
            f"Clause {index:04d} requires the Supplier to submit document {index}."
            for index in range(start, min(start + 3, sentences))
        )
        for start in range(0, sentences, 3)
    ]
    page.insert_text((20, 20), "\n".join(lines), fontsize=4)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.parametrize("length", [0, 1, 3999, 4000])
def test_text_that_fits_is_one_part(length: int) -> None:
    text = "a" * length
    assert segment_spans(text) == [(0, length)]


@pytest.mark.parametrize("length", [4001, 4399, 8000, 9000, 25_000])
def test_longer_text_is_split_within_the_bound_and_covers_every_character(length: int) -> None:
    text = _words(length // 7 + 1)[:length]
    spans = segment_spans(text)

    assert len(spans) >= 2
    assert all(0 < end - start <= MAX_EXCERPT_CHARS for start, end in spans)
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    covered = [False] * len(text)
    for start, end in spans:
        for index in range(start, end):
            covered[index] = True
    assert all(covered), "a stretch of the source fell in no part"
    # reading order: starts and ends both increase
    assert [s for s, _ in spans] == sorted(s for s, _ in spans)
    assert [e for _, e in spans] == sorted(e for _, e in spans)


def test_consecutive_parts_overlap_so_a_boundary_sentence_is_whole_somewhere() -> None:
    text = _sentences(200)
    spans = segment_spans(text)
    for (_, end), (next_start, _) in zip(spans, spans[1:], strict=False):
        assert 0 < end - next_start <= SEGMENT_OVERLAP_CHARS + 50
    sentences = re.findall(r"Clause \d{4} requires[^.]*\.", text)
    parts = segment_text(text)
    for sentence in sentences:
        assert any(sentence in part for part in parts), sentence


def test_split_prefers_a_boundary_over_cutting_a_word() -> None:
    parts = segment_text(_words(2000))
    for part in parts:
        assert all(re.fullmatch(r"w\d{5}", token) for token in part.split())


def test_segmentation_is_deterministic() -> None:
    text = _sentences(300)
    assert segment_text(text) == segment_text(text)


def test_nonsensical_overlap_is_refused() -> None:
    with pytest.raises(ValueError, match="overlap"):
        segment_spans("x" * 100, limit=50, overlap=50)


def test_pdf_page_over_the_bound_keeps_every_requirement_and_its_page() -> None:
    units = extract_units("big.pdf", "application/pdf", io.BytesIO(_dense_pdf(250)))

    # The PDF writer may wrap text; compare by the numbered clauses it can still read back.
    joined = " ".join(unit.text for unit in units)
    seen = set(re.findall(r"Clause (\d{4})", joined))
    assert seen == {f"{index:04d}" for index in range(250)}
    assert len(units) >= 3
    assert all(unit.locator.startswith("p.1") for unit in units)
    assert all(len(unit.text) <= MAX_EXCERPT_CHARS for unit in units)
    assert [unit.locator for unit in units] == [
        f"p.1 (part {i} of {len(units)})" for i in range(1, len(units) + 1)
    ]


def test_pdf_short_pages_keep_the_plain_page_locator() -> None:
    units = extract_units(
        "short.pdf",
        "application/pdf",
        io.BytesIO(_pdf_bytes(["Submit the signed Bid Form with your offer before closing time."])),
    )
    assert [unit.locator for unit in units] == ["p.1"]


def test_docx_long_paragraph_is_segmented_not_truncated() -> None:
    long_paragraph = _sentences(150)
    assert len(long_paragraph) > 2 * MAX_EXCERPT_CHARS
    units = extract_units(
        "terms.docx",
        None,
        io.BytesIO(
            _docx_bytes(
                ["Short first paragraph with enough words to be readable here.", long_paragraph]
            )
        ),
    )
    parts = [unit for unit in units if unit.locator.startswith("para.2")]
    assert len(parts) >= 3
    assert all(len(unit.text) <= MAX_EXCERPT_CHARS for unit in units)
    assert parts[0].locator == f"para.2 (part 1 of {len(parts)})"
    # Every clause number in the original appears in some part (the tail used to be dropped).
    wanted = set(re.findall(r"Clause (\d{4})", long_paragraph))
    found = {n for unit in parts for n in re.findall(r"Clause (\d{4})", unit.text)}
    assert found == wanted
    assert units[0].locator == "para.1"


def test_xlsx_sheet_blocks_are_bounded_and_no_row_is_lost() -> None:
    rows = [
        [f"Item {index:04d}", "Provide a type test report for the equipment " * 2]
        for index in range(150)
    ]
    units = extract_units("matrix.xlsx", None, io.BytesIO(_xlsx_bytes(rows)))

    assert len(units) >= 2
    assert all(len(unit.text) <= MAX_EXCERPT_CHARS for unit in units)
    wanted = {f"Item {index:04d}" for index in range(150)}
    found = {item for unit in units for item in re.findall(r"Item \d{4}", unit.text)}
    assert found == wanted
    assert all(unit.locator.startswith("Sheet") for unit in units)


def test_xlsx_single_oversized_cell_is_segmented() -> None:
    units = extract_units("one.xlsx", None, io.BytesIO(_xlsx_bytes([[_sentences(250)]])))
    assert len(units) >= 3
    assert all(len(unit.text) <= MAX_EXCERPT_CHARS for unit in units)
    assert all("part " in unit.locator for unit in units)


def test_segment_locators_are_stable_and_unique_so_a_restart_resumes_on_the_same_keys() -> None:
    data = _dense_pdf(250)
    first = [u.locator for u in extract_units("big.pdf", None, io.BytesIO(data))]
    second = [u.locator for u in extract_units("big.pdf", None, io.BytesIO(data))]
    assert first == second
    assert len(set(first)) == len(first)


def test_a_discovery_job_analyses_every_part_of_an_over_long_page(tmp_path) -> None:
    import asyncio
    from urllib.parse import urlsplit

    from scripts.asgi_acceptance_ops08 import request
    from tests.unit.test_deliverable_discovery_job_routes import _include, _start
    from tests.unit.test_deliverable_discovery_job_routes import job_app as _fixture
    from tests.unit.test_deliverable_discovery_run_outcomes import FakeClient

    expected = len(extract_units("big.pdf", None, io.BytesIO(_dense_pdf(250))))
    assert expected >= 3

    generator = _fixture.__wrapped__(tmp_path, pytest.MonkeyPatch())
    app = next(generator)
    try:

        async def scenario() -> None:
            await _include(app, "Long", {"big.pdf": _dense_pdf(250)})
            client = FakeClient()
            app.discovery_job_runner.llm_client = client
            _, headers, _ = await _start(app, "long-page")
            job_id = urlsplit(headers["location"]).path.rsplit("/", 1)[-1]
            assert app.discovery_job_runner.run_pending() == 1
            job = app.discovery_job_service.get_job(job_id)
            assert job["state"] == "SUCCEEDED"
            assert job["passages_total"] == expected
            assert job["passages_analysed"] == expected
            assert client.calls == expected
            status, _, page = await request(app.app, "GET", urlsplit(headers["location"]).path)
            assert status == 200 and b"part 1 of" in page

        asyncio.run(scenario())
    finally:
        generator.close()
