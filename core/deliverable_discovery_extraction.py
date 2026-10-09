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
from typing import BinaryIO

MAX_EXCERPT_CHARS = 4000
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


class UnsupportedMediaTypeError(ValueError):
    pass


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
                    units.append(
                        RawUnit(
                            locator_type="PAGE",
                            locator=locator,
                            text=text[:MAX_EXCERPT_CHARS],
                            readable=True,
                        )
                    )
        finally:
            doc.close()
    except Exception as exc:
        units.append(_parse_failure(".pdf", "PAGE", exc))
    return units


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
            units.append(
                RawUnit(
                    locator_type="PARAGRAPH",
                    locator=f"para.{para_index}",
                    text=_clean(text)[:MAX_EXCERPT_CHARS],
                    readable=True,
                )
            )
        for table_index, table in enumerate(document.tables, start=1):
            rows = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                units.append(
                    RawUnit(
                        locator_type="PARAGRAPH",
                        locator=f"table.{table_index}",
                        text=_clean("\n".join(rows))[:MAX_EXCERPT_CHARS],
                        readable=True,
                    )
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
                    if block_start is None:
                        block_start = row_values[0][0]
                    block_end = row_values[-1][0]
                    block.append(" | ".join(f"{coord}: {value}" for coord, value in row_values))
                    if len("\n".join(block)) > MAX_EXCERPT_CHARS:
                        units.append(_sheet_unit(sheet.title, block_start, block_end, block))
                        block, block_start, block_end = [], None, None
                if block:
                    units.append(_sheet_unit(sheet.title, block_start, block_end, block))
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


def _sheet_unit(
    sheet_title: str,
    start: str | None,
    end: str | None,
    block: list[str],
) -> RawUnit:
    start_coord = start or "?"
    end_coord = end or "?"
    locator = (
        f"{sheet_title}!{start_coord}"
        if start_coord == end_coord
        else f"{sheet_title}!{start_coord}:{end_coord}"
    )
    return RawUnit(
        locator_type="SHEET_CELL",
        locator=locator,
        text=_clean("\n".join(block))[:MAX_EXCERPT_CHARS],
        readable=True,
    )
