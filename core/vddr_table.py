"""Deterministic reading of a customer "Schedule of Vendor Drawing and Data Requirements" table.

Plain page text scrambles this table's columns, so applicability and timing cannot be read from
it. ``page.find_tables()`` keeps the header, and each data row is rendered here as one
self-contained ``ROW <item> | KEY=value | ...`` line. That line is the passage persisted for the
finding and the quotation shown to the reviewer, and it is the only input to
``interpret_vddr_unit``: the interpretation never needs the original table and never calls a model.

Applicability is the ``Required?`` column; a tender-stage component is a positive TENDER quantity;
post-award components are positive REVIEW / FINAL / AS BUILT quantities or due values. A row can
have both, and then yields two findings that share a title.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from core.deliverable_discovery_extraction import MAX_EXCERPT_CHARS

_ITEM_CODE = re.compile(r"^\d+(?:\.\d+)+$")
_QTY_DUE = re.compile(r"^(?P<group>.+?)\s+QTY\s+DUE$", re.IGNORECASE)
_POST_AWARD_GROUPS = ("REVIEW", "FINAL", "AS BUILT")
_TIMING_LABELS = {"REVIEW": "Review", "FINAL": "Final", "AS BUILT": "As built"}
SECTION_NAME = "Schedule of Vendor Drawing and Data Requirements"


def _norm(cell: object) -> str:
    return " ".join(str(cell).split()) if cell is not None else ""


def find_vddr_header(rows: Sequence[Sequence[object]]) -> int | None:
    """Index of the header row: it has a ``Required?`` cell and a cell starting ``TENDER``."""
    for index, row in enumerate(rows):
        cells = [_norm(cell).casefold() for cell in row]
        if "required?" in cells and any(cell.startswith("tender") for cell in cells):
            return index
    return None


_KNOWN_NAMES = {
    name.casefold(): name
    for name in (
        "DESCRIPTION",
        "Required?",
        "TENDER QTY",
        "COMMENTS",
        *(f"{group} {unit}" for group in _POST_AWARD_GROUPS for unit in ("QTY", "DUE")),
    )
}


def _canonical_name(name: str) -> str:
    """The spelling the interpreter reads, so a header written ``Tender Qty`` still works."""
    return _KNOWN_NAMES.get(name.casefold(), name)


def expand_header(header: Sequence[object]) -> list[str]:
    """Header names; a ``REVIEW QTY DUE`` cell plus a blank one becomes QTY and DUE columns."""
    names: list[str] = []
    cells = [_norm(cell) for cell in header]
    index = 0
    while index < len(cells):
        cell = cells[index]
        match = _QTY_DUE.match(cell)
        if match:
            group = match.group("group").upper()
            names.append(f"{group} QTY")
            if index + 1 < len(cells) and not cells[index + 1]:
                names.append(f"{group} DUE")
                index += 1
        elif cell:
            names.append(cell)
        else:
            names.append(f"COL{index + 1}")
        index += 1
    names = [_canonical_name(name) for name in names]
    seen: dict[str, int] = {}
    unique: list[str] = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        unique.append(name if seen[name] == 1 else f"{name} {seen[name]}")
    return unique


def _value(cell: object) -> str:
    return _norm(cell).replace("|", "/")


def render_rows(rows: Sequence[Sequence[object]], header_index: int) -> list[str]:
    """One rendered line per data row (rows whose first cell is an item number like ``5.01``)."""
    names = expand_header(rows[header_index])
    lines: list[str] = []
    for row in rows[header_index + 1 :]:
        code = _norm(row[0]) if row else ""
        if not _ITEM_CODE.match(code):
            continue
        parts = [f"ROW {code}"]
        pairs: dict[str, str] = {}
        for name, cell in zip(names[1:], list(row)[1:], strict=False):
            value = _value(cell)
            if value:
                pairs[name] = value
                parts.append(f"{name}={value}")
        timing = _timing_summary(pairs)
        if timing:
            parts.append(f"TIMING={timing}")
        lines.append(" | ".join(parts))
    return lines


def _timing_summary(pairs: dict[str, str]) -> str:
    items = [
        f"{_TIMING_LABELS[group]} {pairs[f'{group} DUE']}"
        for group in _POST_AWARD_GROUPS
        if pairs.get(f"{group} DUE")
    ]
    return "; ".join(items)


def group_lines(lines: Sequence[str], limit: int = MAX_EXCERPT_CHARS) -> list[tuple[str, str, str]]:
    """Group whole lines into passages of at most ``limit`` characters.

    Returns ``(first item, last item, text)``. A line is never split; one longer than ``limit``
    is its own passage.
    """
    groups: list[tuple[str, str, str]] = []
    current: list[str] = []
    for line in lines:
        if current and len("\n".join([*current, line])) > limit:
            groups.append((_item(current[0]), _item(current[-1]), "\n".join(current)))
            current = []
        current.append(line)
    if current:
        groups.append((_item(current[0]), _item(current[-1]), "\n".join(current)))
    return groups


def _item(line: str) -> str:
    return line.split(" | ", 1)[0].removeprefix("ROW ").strip()


@dataclass(frozen=True)
class RowReading:
    """What one rendered row says."""

    code: str
    description: str
    required: bool
    tender_qty: int
    post_award: bool
    timing: str
    fields: dict[str, str] = field(default_factory=dict)


def _positive(value: str | None) -> int:
    match = re.match(r"^\d+", (value or "").strip())
    return int(match.group()) if match else 0


def interpret_row_line(line: str) -> RowReading:
    """Parse one rendered row line. Raises ``ValueError`` if it is not a rendered row."""
    parts = line.strip().split(" | ")
    if not parts or not parts[0].startswith("ROW "):
        raise ValueError(
            f"Not a rendered VDDR row: {line[:60]!r}. Re-run discovery to regenerate it."
        )
    fields: dict[str, str] = {}
    for part in parts[1:]:
        key, separator, value = part.partition("=")
        if separator:
            fields[_canonical_name(key)] = value
    post_award = any(
        _positive(fields.get(f"{group} QTY")) > 0 or bool(fields.get(f"{group} DUE"))
        for group in _POST_AWARD_GROUPS
    )
    return RowReading(
        code=parts[0].removeprefix("ROW ").strip(),
        description=fields.get("DESCRIPTION", ""),
        required=fields.get("Required?", "").strip().upper() == "Y",
        tender_qty=_positive(fields.get("TENDER QTY")),
        post_award=post_award,
        timing=fields.get("TIMING", ""),
        fields=fields,
    )


MAX_QUOTE_CHARS = 1900


def _bounded_quote(line: str) -> str:
    """The rendered row, or its first words when it is too long to store as a quotation.

    The cut is a literal prefix of the row (cut at a word boundary), so it is still found word for
    word in the persisted passage; the interpretation above always reads the whole row.
    """
    if len(line) <= MAX_QUOTE_CHARS:
        return line
    cut = line[:MAX_QUOTE_CHARS]
    space = cut.rfind(" ")
    return (cut[:space] if space > MAX_QUOTE_CHARS // 2 else cut).rstrip(" |")


def _finding(
    reading: RowReading,
    line: str,
    *,
    lifecycle: str,
    status: str,
    description: str,
    timing: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "title": reading.description or f"Vendor data item {reading.code}",
        "description": description[:300],
        "lifecycle": lifecycle,
        "requirement_status": status,
        "item_kind": "SUBMISSION_DOCUMENT",
        "category": "Vendor drawing/data",
        "condition_text": None,
        "applies_at": None,
        "responsible_party": None,
        "timing_text": timing,
        "due_date": None,
        "dependencies": None,
        "confidence": "HIGH",
        "review_reason": reason,
        "source_section": f"{SECTION_NAME}, item {reading.code}",
        "source_quote": _bounded_quote(line),
    }


def interpret_vddr_unit(text: str) -> list[dict[str, Any]]:
    """Deterministic findings for every rendered row line in ``text`` (no model involved)."""
    findings: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("ROW "):
            continue
        reading = interpret_row_line(line)
        code = reading.code
        if not reading.required:
            findings.append(
                _finding(
                    reading,
                    line,
                    lifecycle="NOT_APPLICABLE",
                    status="OPTIONAL",
                    description=f"Vendor data item {code} is not marked Required in the schedule.",
                    reason="VDDR row not marked Required",
                )
            )
            continue
        if reading.tender_qty:
            findings.append(
                _finding(
                    reading,
                    line,
                    lifecycle="BID_RETURNABLE",
                    status="REQUIRED",
                    description=(
                        f"Vendor drawing/data item {code} to be supplied with the tender "
                        f"(qty {reading.tender_qty})."
                    ),
                )
            )
        if reading.post_award:
            findings.append(
                _finding(
                    reading,
                    line,
                    lifecycle="POST_AWARD",
                    status="REQUIRED",
                    description=f"Vendor drawing/data item {code} is due after order placement.",
                    timing=reading.timing or None,
                )
            )
        if not reading.tender_qty and not reading.post_award:
            findings.append(
                _finding(
                    reading,
                    line,
                    lifecycle="NEEDS_REVIEW",
                    status="REQUIRED",
                    description=f"Vendor data item {code} is marked Required without a stage.",
                    reason=(
                        "Marked Required but no tender, review, final or as-built quantity is given"
                    ),
                )
            )
    return findings
