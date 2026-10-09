"""Plain-language presentation of discovery job, run and file outcomes.

Pure: no database, network or model access. Stored outcome codes stay unchanged; this
module only chooses the words shown to the owner and whether a retry can help.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.analysis_capability import (
    COULD_NOT_OPEN_LABEL,
    NO_TEXT_LABEL,
    NO_TEXT_PDF_LABEL,
    UNSUPPORTED_LABEL,
)
from core.deliverable_discovery_llm import TRUNCATED_REASON_PREFIX
from core.deliverable_discovery_outcomes import FileOutcomeKind

OutcomeGroup = Literal["analysed", "partly", "not_analysed", "not_readable"]

_JOB_STATE_LABELS = {
    "QUEUED": "Waiting to start",
    "RUNNING": "Analysing",
    "CANCEL_REQUESTED": "Stopping",
    "SUCCEEDED": "Analysis complete",
    "PARTIAL": "Analysis partly complete",
    "FAILED": "Analysis did not produce results",
    "CANCELLED": "Analysis cancelled",
    "ENDPOINT_ERROR": "Paused — the model could not be reached",
}
_NO_TEXT_MARKERS = ("ocr may be needed", "scanned image", "extractable words")
_EMPTY_DOCUMENT_MARKERS = ("no extractable paragraph", "no non-empty cells")
NO_RETRY_REASON = (
    "Retrying will not help: the files that are not analysed cannot be read by the analysis "
    "(unsupported type or no readable text)."
)


class FileOutcomeView(BaseModel):
    """One file's analysis result in the owner's words."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    filename: str
    status: str
    reason: str
    remedy: str
    retryable: bool
    detail: str
    group: OutcomeGroup


class DiscoveryCounts(BaseModel):
    """File and passage counts that separate extracted text from model analysis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    files_total: int = Field(ge=0)
    files_with_text: int = Field(ge=0)
    files_fully_analysed: int = Field(ge=0)
    files_partly_analysed: int = Field(ge=0)
    files_not_analysed: int = Field(ge=0)
    files_not_readable: int = Field(ge=0)
    passages_extracted: int = Field(ge=0)
    passages_analysed: int = Field(ge=0)
    passages_unusable: int = Field(ge=0)
    passages_not_reached: int = Field(ge=0)


def _count(row: Mapping[str, object], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def job_state_label(state: str, error_code: str | None = None) -> str:
    """Return the plain wording for a discovery job state."""
    if error_code == "NOTHING_NEW":
        return "Nothing new to analyse"
    return _JOB_STATE_LABELS.get(state, state.replace("_", " ").capitalize())


def _failure_reason(row: Mapping[str, object], detail: str) -> str:
    if TRUNCATED_REASON_PREFIX in detail:
        return "The model's answer was cut off before it finished."
    if _count(row, "passages_invalid_output"):
        return "The model's answer was not in the expected form."
    if _count(row, "passages_model_failed"):
        return "The model server did not answer."
    if _count(row, "passages_total") > _count(row, "model_calls"):
        return "The analysis stopped before it reached every passage."
    return ""


def file_outcome_view(row: Mapping[str, object]) -> FileOutcomeView:
    """Translate one stored file outcome into status, reason and remedy."""
    filename = str(row.get("original_filename") or "Unknown file")
    detail = str(row.get("detail") or "")
    analysed = _count(row, "passages_analysed")
    total = _count(row, "passages_total")

    def view(
        status: str, group: OutcomeGroup, *, reason: str = "", remedy: str = "", retry: bool = False
    ) -> FileOutcomeView:
        return FileOutcomeView(
            filename=filename,
            status=status,
            reason=reason,
            remedy=remedy,
            retryable=retry,
            detail=detail,
            group=group,
        )

    try:
        outcome = FileOutcomeKind(str(row.get("outcome") or ""))
    except ValueError:
        return view("Outcome not recognised", "not_analysed")

    if outcome is FileOutcomeKind.ANALYSED_CANDIDATES:
        return view("Analysed — possible returnables found", "analysed")
    if outcome is FileOutcomeKind.ANALYSED_ZERO_CANDIDATES:
        return view("Analysed — no returnables found in this file", "analysed")
    if outcome is FileOutcomeKind.PARTIALLY_ANALYSED and analysed > 0:
        return view(
            f"Partly analysed — {analysed} of {total} passage(s)",
            "partly",
            reason=_failure_reason(row, detail),
            remedy="Retry to analyse the rest.",
            retry=True,
        )
    if outcome in {FileOutcomeKind.PARTIALLY_ANALYSED, FileOutcomeKind.INVALID_MODEL_OUTPUT}:
        return view(
            "Not analysed — the model's answer could not be used",
            "not_analysed",
            reason=_failure_reason(row, detail),
            remedy="Retry the analysis.",
            retry=True,
        )
    if outcome is FileOutcomeKind.MODEL_UNAVAILABLE:
        return view(
            "Not analysed — the model could not be reached",
            "not_analysed",
            reason="The model server did not answer.",
            remedy="Check that the model server is running, then retry.",
            retry=True,
        )
    if outcome is FileOutcomeKind.NOT_ATTEMPTED:
        return view(
            "Not analysed yet",
            "not_analysed",
            reason="The analysis stopped before it reached this file.",
            remedy="Retry the analysis.",
            retry=True,
        )
    if outcome is FileOutcomeKind.UNSUPPORTED_TYPE:
        return view(
            UNSUPPORTED_LABEL,
            "not_readable",
            remedy="Review this file yourself, or add a PDF, Word or Excel copy.",
        )
    if outcome is FileOutcomeKind.UNREADABLE:
        if any(marker in detail.lower() for marker in _NO_TEXT_MARKERS):
            return view(
                NO_TEXT_PDF_LABEL,
                "not_readable",
                remedy="Review this file yourself, or add a copy with selectable text.",
            )
        return view(
            COULD_NOT_OPEN_LABEL,
            "not_readable",
            remedy="Check that the file opens on your computer, then add a working copy.",
        )
    return view(NO_TEXT_LABEL, "not_readable", remedy="Review this file yourself.")


def discovery_counts(rows: Iterable[Mapping[str, object]]) -> DiscoveryCounts:
    """Count files by result group and passages by what happened to them."""
    items = list(rows)
    groups = [file_outcome_view(row).group for row in items]
    return DiscoveryCounts(
        files_total=len(items),
        files_with_text=sum(_count(row, "passages_total") > 0 for row in items),
        files_fully_analysed=groups.count("analysed"),
        files_partly_analysed=groups.count("partly"),
        files_not_analysed=groups.count("not_analysed"),
        files_not_readable=groups.count("not_readable"),
        passages_extracted=sum(_count(row, "passages_total") for row in items),
        passages_analysed=sum(_count(row, "passages_analysed") for row in items),
        passages_unusable=sum(
            _count(row, "passages_model_failed") + _count(row, "passages_invalid_output")
            for row in items
        ),
        passages_not_reached=sum(
            max(0, _count(row, "passages_total") - _count(row, "model_calls")) for row in items
        ),
    )


def files_sentence(counts: DiscoveryCounts) -> str:
    """Say how many files had text extracted and how many the model analysed."""
    return (
        f"Text extracted from {counts.files_with_text} of {counts.files_total} file(s). "
        f"Analysed by the model: {counts.files_fully_analysed} fully, "
        f"{counts.files_partly_analysed} partly, {counts.files_not_analysed} not yet. "
        f"Could not be read: {counts.files_not_readable}."
    )


def passages_sentence(counts: DiscoveryCounts) -> str:
    """Say what happened to the extracted passages."""
    return (
        f"{counts.passages_extracted} passage(s) of text extracted (pages, sections or sheets). "
        f"Analysed by the model: {counts.passages_analysed}. "
        f"No usable answer: {counts.passages_unusable}. "
        f"Not reached: {counts.passages_not_reached}."
    )


def unreadable_reason_label(failure_reason: str | None) -> str:
    """Return the plain label for one stored extraction failure."""
    text = (failure_reason or "").lower()
    if any(marker in text for marker in _NO_TEXT_MARKERS):
        return NO_TEXT_PDF_LABEL
    if any(marker in text for marker in _EMPTY_DOCUMENT_MARKERS):
        return NO_TEXT_LABEL
    return COULD_NOT_OPEN_LABEL


def progress_sentence(progress: Mapping[str, object]) -> str:
    """Summarise Bid-level coverage across every analysis run so far."""
    fully = _count(progress, "fully_analysed")
    partly = _count(progress, "partly_analysed")
    not_readable = _count(progress, "not_readable")
    waiting = _count(progress, "recoverable") + _count(progress, "never_attempted")
    total = fully + partly + not_readable + waiting
    return (
        f"{fully} of {total} Included file(s) fully analysed; {partly} partly analysed; "
        f"{waiting} not yet analysed; {not_readable} could not be read."
    )


def discovery_job_view(
    job: Mapping[str, object],
    file_outcomes: Iterable[Mapping[str, object]],
    *,
    retry_worthwhile: bool,
) -> dict[str, object]:
    """Build the display facts for one discovery job page."""
    rows = list(file_outcomes)
    counts = discovery_counts(rows)
    state = str(job.get("state") or "")
    error_code = job.get("error_code")
    unfinished = state in {"PARTIAL", "FAILED"}
    offer_retry = unfinished and retry_worthwhile
    return {
        "state_label": job_state_label(state, str(error_code) if error_code else None),
        "files": [file_outcome_view(row).model_dump() for row in rows],
        "counts": counts.model_dump(),
        "files_sentence": files_sentence(counts) if rows else "",
        "passages_sentence": passages_sentence(counts) if rows else "",
        "offer_retry": offer_retry,
        "no_retry_reason": NO_RETRY_REASON if unfinished and not offer_retry else "",
    }


__all__ = [
    "NO_RETRY_REASON",
    "DiscoveryCounts",
    "FileOutcomeView",
    "discovery_counts",
    "discovery_job_view",
    "file_outcome_view",
    "files_sentence",
    "job_state_label",
    "passages_sentence",
    "progress_sentence",
    "unreadable_reason_label",
]
