"""OPS-13A — deterministic extraction preserving page/paragraph/sheet-cell provenance.

`core.document_processor.DocumentProcessor` is intentionally not reused here: it joins page
text into one string and drops per-page locators, which this feature's provenance requirement
needs. This module extracts the same underlying file types with the same already-installed
libraries (PyMuPDF, python-docx, openpyxl) but keeps one located unit per page/paragraph/cell
range instead of one flattened string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

# One passage is at most this many characters: the bound the model request and the stored
# extraction row were sized for. Text longer than this is split into several passages, never cut.
MAX_EXCERPT_CHARS = 4000
# Consecutive parts of one over-long unit share this many characters so that a sentence that
# straddles a split is whole in at least one part.
SEGMENT_OVERLAP_CHARS = 400
MIN_READABLE_WORDS = 10
_LOCATOR_TYPE_BY_SUFFIX = {
    ".pdf": "PAGE",
    ".docx": "PARAGRAPH",
    ".doc": "PARAGRAPH",
    ".xlsx": "SHEET_CELL",
    ".xlsm": "SHEET_CELL",
}


@dataclass(frozen=True)
class RawUnit:
    locator_type: str
    locator: str
    text: str
    readable: bool
    failure_reason: str | None = None
    document: str = ""
    """The file's name or relative path, shown to the model as context. Set by the runners."""
    kind: str = "TEXT"
    """``TEXT`` is read by the model; ``VDDR_TABLE`` is rendered table rows read by rules."""


VDDR_TABLE_KIND = "VDDR_TABLE"


class UnsupportedMediaTypeError(ValueError):
    pass


def segment_spans(
    text: str, limit: int = MAX_EXCERPT_CHARS, overlap: int = SEGMENT_OVERLAP_CHARS
) -> list[tuple[int, int]]:
    """Return ``(start, end)`` spans of ``text`` of at most ``limit`` characters.

    Spans are in reading order, overlap by about ``overlap`` characters, and together cover every
    character of ``text``. A split prefers a paragraph, line, sentence or word boundary in the last
    30 % of a window. A text that fits in ``limit`` is one span.
    """
    if limit <= 0 or overlap < 0 or overlap >= int(limit * 0.7):
        raise ValueError("segment limit must be positive and the overlap well below it")
    length = len(text)
    if length <= limit:
        return [(0, length)]
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        end = min(start + limit, length)
        if end < length:
            floor = start + int(limit * 0.7)
            for separator in ("\n\n", "\n", ". ", " "):
                cut = text.rfind(separator, floor, end)
                if cut >= 0:
                    end = cut + len(separator)
                    break
        spans.append((start, end))
        if end >= length:
            return spans
        next_start = max(end - overlap, start + 1)
        # Do not begin a part in the middle of a word when a boundary is within the overlap.
        boundary = text.find(" ", next_start, end)
        start = boundary + 1 if boundary >= 0 else next_start


def segment_text(
    text: str, limit: int = MAX_EXCERPT_CHARS, overlap: int = SEGMENT_OVERLAP_CHARS
) -> list[str]:
    """Split ``text`` into ordered, overlapping, non-empty parts of at most ``limit`` characters."""
    parts = [text[a:b].strip() for a, b in segment_spans(text, limit, overlap)]
    return [part for part in parts if part]


def _units_from_text(locator_type: str, locator: str, text: str) -> list[RawUnit]:
    """One unit when the text fits; otherwise ordered parts that keep the original locator."""
    parts = segment_text(text)
    if len(parts) <= 1:
        return [
            RawUnit(
                locator_type=locator_type,
                locator=locator,
                text=parts[0] if parts else "",
                readable=bool(parts),
            )
        ]
    return [
        RawUnit(
            locator_type=locator_type,
            locator=f"{locator} (part {index} of {len(parts)})",
            text=part,
            readable=True,
        )
        for index, part in enumerate(parts, start=1)
    ]


def extract_units(filename: str, media_type: str | None, handle: BinaryIO) -> list[RawUnit]:
    """Dispatch on file extension. Raises UnsupportedMediaTypeError for anything else."""
    suffix = Path(filename).suffix.lower()
    locator_type_for_filename(filename)
    data = handle.read()
    if suffix == ".pdf":
        return _extract_pdf(data)
    if suffix in (".docx", ".doc"):
        return _extract_docx(data, suffix)
    if suffix in (".xlsx", ".xlsm"):
        return _extract_xlsx(data, suffix)
    raise UnsupportedMediaTypeError(
        f"deliverable discovery extraction does not support {suffix or '(no extension)'} "
        f"for {filename!r}"
    )


def locator_type_for_filename(filename: str) -> str:
    """Return the extraction locator type for a supported filename."""
    suffix = Path(filename).suffix.lower()
    try:
        return _LOCATOR_TYPE_BY_SUFFIX[suffix]
    except KeyError as exc:
        raise UnsupportedMediaTypeError(
            f"deliverable discovery extraction does not support {suffix or '(no extension)'} "
            f"for {filename!r}"
        ) from exc


def _parse_failure(suffix: str, locator_type: str, exc: Exception) -> RawUnit:
    format_name = suffix.removeprefix(".").upper()
    return RawUnit(
        locator_type=locator_type,
        locator="file",
        text="",
        readable=False,
        failure_reason=(
            f"file could not be parsed as {format_name} ({type(exc).__name__}); "
            f"it is corrupt or not really a {format_name}; re-obtain the file"
        )[:500],
    )


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_pdf(data: bytes) -> list[RawUnit]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise ImportError("PyMuPDF not installed. Run: uv add pymupdf") from exc

    units: list[RawUnit] = []
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            for page_index, page in enumerate(doc, start=1):
                table_units = _vddr_table_units(page, page_index)
                if table_units:
                    units.extend(table_units)
                    continue
                text = _clean(page.get_text("text"))
                word_count = len(text.split())
                locator = f"p.{page_index}"
                if word_count < MIN_READABLE_WORDS:
                    units.append(
                        RawUnit(
                            locator_type="PAGE",
                            locator=locator,
                            text="",
                            readable=False,
                            failure_reason=(
                                "no readable text found on this page (fewer than "
                                f"{MIN_READABLE_WORDS} words) — OCR may be needed"
                            ),
                        )
                    )
                else:
                    units.extend(_units_from_text("PAGE", locator, text))
        finally:
            doc.close()
    except Exception as exc:
        units.append(_parse_failure(".pdf", "PAGE", exc))
    return units


def _vddr_table_units(page: Any, page_index: int) -> list[RawUnit]:
    """Rendered-row units for a VDDR table on ``page``; empty when there is none.

    Any failure of table detection falls back to the page's plain text, as before.
    """
    from core.vddr_table import find_vddr_header, group_lines, render_rows

    try:
        rows: list[list[object]] = []
        for table in page.find_tables().tables:
            extracted = table.extract()
            if find_vddr_header(extracted) is not None:
                rows.extend(extracted)
        header_index = find_vddr_header(rows)
        if header_index is None:
            return []
        lines = render_rows(rows, header_index)
    except Exception:
        return []
    return [
        RawUnit(
            locator_type="PAGE",
            locator=f"p.{page_index} rows {first}-{last}"
            if first != last
            else f"p.{page_index} row {first}",
            text=text,
            readable=True,
            kind=VDDR_TABLE_KIND,
        )
        for first, last, text in group_lines(lines)
    ]


def _extract_docx(data: bytes, suffix: str) -> list[RawUnit]:
    import io

    try:
        from docx import Document
    except ImportError as exc:
        raise ImportError("python-docx not installed. Run: uv add python-docx") from exc

    units: list[RawUnit] = []
    try:
        document = Document(io.BytesIO(data))
        para_index = 0
        for para in document.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            para_index += 1
            units.extend(_units_from_text("PARAGRAPH", f"para.{para_index}", _clean(text)))
        for table_index, table in enumerate(document.tables, start=1):
            rows = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                units.extend(
                    _units_from_text("PARAGRAPH", f"table.{table_index}", _clean("\n".join(rows)))
                )
        if not units:
            units.append(
                RawUnit(
                    locator_type="PARAGRAPH",
                    locator="document",
                    text="",
                    readable=False,
                    failure_reason="no extractable paragraph or table text was found in this DOCX",
                )
            )
    except Exception as exc:
        units.append(_parse_failure(suffix, "PARAGRAPH", exc))
    return units


def _extract_xlsx(data: bytes, suffix: str) -> list[RawUnit]:
    import io

    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ImportError("openpyxl not installed. Run: uv add openpyxl") from exc

    units: list[RawUnit] = []
    try:
        workbook = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        try:
            for sheet in workbook.worksheets:
                block: list[str] = []
                block_start: str | None = None
                block_end: str | None = None
                for row in sheet.iter_rows():
                    row_values = [
                        (cell.coordinate, str(cell.value).strip())
                        for cell in row
                        if cell.value is not None and str(cell.value).strip()
                    ]
                    if not row_values:
                        continue
                    row_text = " | ".join(f"{coord}: {value}" for coord, value in row_values)
                    if block and len("\n".join([*block, row_text])) > MAX_EXCERPT_CHARS:
                        # Close the block before the row that would overflow it, so the row
                        # starts the next block whole.
                        units.extend(_sheet_units(sheet.title, block_start, block_end, block))
                        block, block_start = [], None
                    if block_start is None:
                        block_start = row_values[0][0]
                    block_end = row_values[-1][0]
                    block.append(row_text)
                if block:
                    units.extend(_sheet_units(sheet.title, block_start, block_end, block))
            if not units:
                units.append(
                    RawUnit(
                        locator_type="SHEET_CELL",
                        locator="workbook",
                        text="",
                        readable=False,
                        failure_reason="workbook has no non-empty cells in any sheet",
                    )
                )
        finally:
            workbook.close()
    except Exception as exc:
        units.append(_parse_failure(suffix, "SHEET_CELL", exc))
    return units


def _sheet_units(
    sheet_title: str,
    start: str | None,
    end: str | None,
    block: list[str],
) -> list[RawUnit]:
    start_coord = start or "?"
    end_coord = end or "?"
    locator = (
        f"{sheet_title}!{start_coord}"
        if start_coord == end_coord
        else f"{sheet_title}!{start_coord}:{end_coord}"
    )
    return _units_from_text("SHEET_CELL", locator, _clean("\n".join(block)))
