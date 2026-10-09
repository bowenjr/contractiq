"""Pure analysis-capability classification for package source files."""

from __future__ import annotations

import io
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from core.deliverable_discovery_extraction import (
    UnsupportedMediaTypeError,
    extract_units,
    locator_type_for_filename,
)

READABLE_LABEL = "Can be analysed"
UNSUPPORTED_LABEL = "Stored only — this file type cannot be analysed (PDF, Word and Excel only)"
NO_TEXT_PDF_LABEL = "No readable text found — OCR may be needed"
NO_TEXT_LABEL = "No readable text found"
COULD_NOT_OPEN_LABEL = "Could not be opened — the file may be damaged or password-protected"
NOT_CHECKED_LABEL = "Not checked in this preview — it will be checked when analysed"


class AnalysisCapability(StrEnum):
    """The established analysis capability of one file."""

    READABLE = "READABLE"
    UNSUPPORTED = "UNSUPPORTED"
    NO_READABLE_TEXT = "NO_READABLE_TEXT"
    COULD_NOT_OPEN = "COULD_NOT_OPEN"
    NOT_CHECKED = "NOT_CHECKED"


@dataclass(frozen=True)
class CapabilityResult:
    """Display-ready result of a deterministic file capability check."""

    capability: AnalysisCapability
    label: str
    type_label: str
    readable_parts: int = 0
    unreadable_parts: int = 0


def is_supported_type(filename: str) -> bool:
    """Return whether the filename has a supported extraction type."""
    try:
        locator_type_for_filename(filename)
    except UnsupportedMediaTypeError:
        return False
    return True


def type_label(filename: str) -> str:
    """Return a plain-language type label derived from the filename."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return "PDF"
    if suffix in {".doc", ".docx"}:
        return "Word document"
    if suffix in {".xlsx", ".xlsm"}:
        return "Excel workbook"
    if suffix:
        return f"{suffix.removeprefix('.').upper()} file"
    return "File"


def not_checked(filename: str) -> CapabilityResult:
    """Return the honest unchecked state for a supported or unsupported file."""
    label = type_label(filename)
    if not is_supported_type(filename):
        return CapabilityResult(AnalysisCapability.UNSUPPORTED, UNSUPPORTED_LABEL, label)
    return CapabilityResult(AnalysisCapability.NOT_CHECKED, NOT_CHECKED_LABEL, label)


def assess_capability(filename: str, data: bytes) -> CapabilityResult:
    """Classify whether extracted content from one file can be analysed."""
    label = type_label(filename)
    if not is_supported_type(filename):
        return CapabilityResult(AnalysisCapability.UNSUPPORTED, UNSUPPORTED_LABEL, label)

    try:
        units = extract_units(filename, None, io.BytesIO(data))
    except ImportError:
        raise
    except UnsupportedMediaTypeError:
        return CapabilityResult(AnalysisCapability.UNSUPPORTED, UNSUPPORTED_LABEL, label)
    except Exception:
        return CapabilityResult(
            AnalysisCapability.COULD_NOT_OPEN,
            COULD_NOT_OPEN_LABEL,
            label,
        )

    readable = [unit for unit in units if unit.readable and unit.text.strip()]
    unreadable_count = sum(not unit.readable for unit in units)
    if readable:
        result_label = READABLE_LABEL
        if label == "PDF" and unreadable_count:
            result_label += (
                f" — {unreadable_count} of {len(readable) + unreadable_count} "
                "page(s) have no readable text"
            )
        return CapabilityResult(
            AnalysisCapability.READABLE,
            result_label,
            label,
            readable_parts=len(readable),
            unreadable_parts=unreadable_count,
        )

    if any(unit.locator == "file" and not unit.readable for unit in units):
        return CapabilityResult(
            AnalysisCapability.COULD_NOT_OPEN,
            COULD_NOT_OPEN_LABEL,
            label,
            unreadable_parts=unreadable_count,
        )

    no_text_label = NO_TEXT_PDF_LABEL if label == "PDF" else NO_TEXT_LABEL
    return CapabilityResult(
        AnalysisCapability.NO_READABLE_TEXT,
        no_text_label,
        label,
        unreadable_parts=len(units),
    )


__all__ = [
    "COULD_NOT_OPEN_LABEL",
    "NOT_CHECKED_LABEL",
    "NO_TEXT_LABEL",
    "NO_TEXT_PDF_LABEL",
    "READABLE_LABEL",
    "UNSUPPORTED_LABEL",
    "AnalysisCapability",
    "CapabilityResult",
    "assess_capability",
    "is_supported_type",
    "not_checked",
    "type_label",
]
