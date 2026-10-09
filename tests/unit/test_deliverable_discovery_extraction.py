"""Synthetic-fixture tests for OPS-13A deterministic extraction. No real Bid package files are
used or committed — every document is built in-memory at test time."""

import io

import pytest

from core.deliverable_discovery_extraction import UnsupportedMediaTypeError, extract_units


def _pdf_bytes(pages: list[str]) -> bytes:
    import fitz

    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document

    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_pdf_extraction_preserves_page_locators_and_flags_scanned_page() -> None:
    readable_page = "Submit the completed Bid Form signed by an authorized representative. " * 3
    data = _pdf_bytes([readable_page, ""])  # second page has no text — simulates a scanned page
    units = extract_units("instructions.pdf", "application/pdf", io.BytesIO(data))

    assert units[0].locator == "p.1"
    assert units[0].readable is True
    assert "Bid Form" in units[0].text

    assert units[1].locator == "p.2"
    assert units[1].readable is False
    assert units[1].failure_reason is not None
    assert units[1].failure_reason == (
        "no readable text found on this page (fewer than 10 words) — OCR may be needed"
    )


def test_docx_extraction_preserves_paragraph_and_table_locators() -> None:
    data = _docx_bytes(
        [
            "This specification requires a technical schedule with the submission.",
            "",
            "Second paragraph with real content for provenance testing.",
        ]
    )
    units = extract_units("specification.docx", None, io.BytesIO(data))
    locators = [u.locator for u in units]
    assert "para.1" in locators
    assert "para.2" in locators
    assert all(u.readable for u in units)


def test_xlsx_extraction_preserves_sheet_cell_locators() -> None:
    data = _xlsx_bytes([["Item", "Price"], ["Pump", "1000"], ["Valve", "250"]])
    units = extract_units("pricing.xlsx", None, io.BytesIO(data))
    assert len(units) == 1
    assert units[0].locator.startswith("Sheet!")
    assert "Pump" in units[0].text
    assert units[0].readable is True


def test_empty_xlsx_is_recorded_unreadable_not_silently_absent() -> None:
    data = _xlsx_bytes([])
    units = extract_units("blank.xlsx", None, io.BytesIO(data))
    assert len(units) == 1
    assert units[0].readable is False
    assert units[0].failure_reason


def test_unsupported_media_type_raises() -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        extract_units("notes.txt", "text/plain", io.BytesIO(b"hello"))
