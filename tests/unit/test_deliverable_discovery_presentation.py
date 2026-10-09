from __future__ import annotations

import pytest

from core.analysis_capability import (
    COULD_NOT_OPEN_LABEL,
    NO_TEXT_LABEL,
    NO_TEXT_PDF_LABEL,
    UNSUPPORTED_LABEL,
)
from core.deliverable_discovery_outcomes import FileOutcomeKind
from core.deliverable_discovery_presentation import (
    NO_RETRY_REASON,
    discovery_counts,
    discovery_job_view,
    file_outcome_view,
    files_sentence,
    job_state_label,
    passages_sentence,
    progress_sentence,
    unreadable_reason_label,
)


def _row(outcome: str, **values: object) -> dict[str, object]:
    row: dict[str, object] = {
        "original_filename": "ITB.pdf",
        "outcome": outcome,
        "passages_total": 0,
        "model_calls": 0,
        "passages_analysed": 0,
        "passages_model_failed": 0,
        "passages_invalid_output": 0,
        "detail": "stored technical detail",
    }
    row.update(values)
    return row


@pytest.mark.parametrize("outcome", list(FileOutcomeKind))
def test_every_stored_outcome_has_plain_wording(outcome: FileOutcomeKind) -> None:
    view = file_outcome_view(_row(outcome.value, passages_total=2, passages_analysed=1))

    assert view.status
    assert "_" not in view.status
    assert view.status != view.status.upper()
    assert "scanned image" not in view.status
    assert view.detail == "stored technical detail"


@pytest.mark.parametrize(
    ("row", "status", "retryable", "group"),
    [
        (
            _row("ANALYSED_CANDIDATES", passages_total=2, model_calls=2, passages_analysed=2),
            "Analysed — possible returnables found",
            False,
            "analysed",
        ),
        (
            _row("ANALYSED_ZERO_CANDIDATES", passages_total=1, model_calls=1, passages_analysed=1),
            "Analysed — no returnables found in this file",
            False,
            "analysed",
        ),
        (
            _row("PARTIALLY_ANALYSED", passages_total=3, model_calls=3, passages_analysed=2),
            "Partly analysed — 2 of 3 passage(s)",
            True,
            "partly",
        ),
        (
            _row("PARTIALLY_ANALYSED", passages_total=3, model_calls=3),
            "Not analysed — the model's answer could not be used",
            True,
            "not_analysed",
        ),
        (
            _row("INVALID_MODEL_OUTPUT", passages_total=1, model_calls=1),
            "Not analysed — the model's answer could not be used",
            True,
            "not_analysed",
        ),
        (
            _row("MODEL_UNAVAILABLE", passages_total=1, model_calls=1),
            "Not analysed — the model could not be reached",
            True,
            "not_analysed",
        ),
        (_row("NOT_ATTEMPTED", passages_total=4), "Not analysed yet", True, "not_analysed"),
        (_row("UNSUPPORTED_TYPE"), UNSUPPORTED_LABEL, False, "not_readable"),
        (
            _row(
                "UNREADABLE", detail="p.1: no readable text found on this page — OCR may be needed"
            ),
            NO_TEXT_PDF_LABEL,
            False,
            "not_readable",
        ),
        (
            _row(
                "UNREADABLE",
                detail="page has fewer than 10 extractable words — likely a scanned image page",
            ),
            NO_TEXT_PDF_LABEL,
            False,
            "not_readable",
        ),
        (
            _row("UNREADABLE", detail="file: could not be parsed"),
            COULD_NOT_OPEN_LABEL,
            False,
            "not_readable",
        ),
        (_row("EXTRACTED_NO_TEXT"), NO_TEXT_LABEL, False, "not_readable"),
        (_row("SOMETHING_NEW"), "Outcome not recognised", False, "not_analysed"),
    ],
)
def test_outcome_mapping(row: dict[str, object], status: str, retryable: bool, group: str) -> None:
    view = file_outcome_view(row)

    assert (view.status, view.retryable, view.group) == (status, retryable, group)
    assert bool(view.remedy) is (group != "analysed" and status != "Outcome not recognised")


def test_failure_reasons_are_actionable_and_ordered() -> None:
    truncated = _row(
        "INVALID_MODEL_OUTPUT",
        passages_total=1,
        model_calls=1,
        passages_invalid_output=1,
        detail="The model returned unusable output for all 1 passage(s); truncated: the model "
        "reply reached the 8192-token output limit (after one retry)",
    )
    invalid = _row(
        "INVALID_MODEL_OUTPUT", passages_total=1, model_calls=1, passages_invalid_output=1
    )
    server = _row("PARTIALLY_ANALYSED", passages_total=2, model_calls=2, passages_model_failed=2)
    stopped = _row("PARTIALLY_ANALYSED", passages_total=3, model_calls=1, passages_analysed=1)

    assert (
        file_outcome_view(truncated).reason == "The model's answer was cut off before it finished."
    )
    assert file_outcome_view(invalid).reason == "The model's answer was not in the expected form."
    assert file_outcome_view(server).reason == "The model server did not answer."
    assert (
        file_outcome_view(stopped).reason == "The analysis stopped before it reached every passage."
    )


def test_sparse_row_never_raises() -> None:
    view = file_outcome_view({"outcome": "PARTIALLY_ANALYSED", "passages_total": "x"})

    assert view.filename == "Unknown file"
    assert view.group == "not_analysed"


def test_counts_separate_extracted_text_from_model_analysis() -> None:
    rows = [
        _row("ANALYSED_CANDIDATES", passages_total=2, model_calls=2, passages_analysed=2),
        _row(
            "PARTIALLY_ANALYSED",
            passages_total=3,
            model_calls=2,
            passages_analysed=1,
            passages_invalid_output=1,
        ),
        _row("MODEL_UNAVAILABLE", passages_total=2, model_calls=2, passages_model_failed=2),
        _row("UNSUPPORTED_TYPE"),
        _row("UNREADABLE", detail="OCR may be needed"),
    ]

    counts = discovery_counts(iter(rows))

    assert (
        counts.files_fully_analysed
        + counts.files_partly_analysed
        + counts.files_not_analysed
        + counts.files_not_readable
        == counts.files_total
        == 5
    )
    assert (
        counts.passages_analysed + counts.passages_unusable + counts.passages_not_reached
        == counts.passages_extracted
        == 7
    )
    assert files_sentence(counts) == (
        "Text extracted from 3 of 5 file(s). Analysed by the model: 1 fully, 1 partly, "
        "1 not yet. Could not be read: 2."
    )
    assert passages_sentence(counts) == (
        "7 passage(s) of text extracted (pages, sections or sheets). Analysed by the model: 3. "
        "No usable answer: 3. Not reached: 1."
    )


@pytest.mark.parametrize(
    ("state", "error_code", "label"),
    [
        ("QUEUED", None, "Waiting to start"),
        ("RUNNING", None, "Analysing"),
        ("CANCEL_REQUESTED", None, "Stopping"),
        ("SUCCEEDED", None, "Analysis complete"),
        ("PARTIAL", None, "Analysis partly complete"),
        ("FAILED", None, "Analysis did not produce results"),
        ("CANCELLED", None, "Analysis cancelled"),
        ("ENDPOINT_ERROR", None, "Paused — the model could not be reached"),
        ("SUCCEEDED", "NOTHING_NEW", "Nothing new to analyse"),
        ("SOME_STATE", None, "Some state"),
    ],
)
def test_job_state_labels(state: str, error_code: str | None, label: str) -> None:
    assert job_state_label(state, error_code) == label


def test_unreadable_reason_labels() -> None:
    assert unreadable_reason_label("no readable text … — OCR may be needed") == NO_TEXT_PDF_LABEL
    assert unreadable_reason_label("likely a scanned image page") == NO_TEXT_PDF_LABEL
    assert unreadable_reason_label("workbook has no non-empty cells in any sheet") == NO_TEXT_LABEL
    assert unreadable_reason_label("PDF could not be parsed") == COULD_NOT_OPEN_LABEL
    assert unreadable_reason_label(None) == COULD_NOT_OPEN_LABEL


def test_progress_sentence() -> None:
    progress = {
        "fully_analysed": 7,
        "partly_analysed": 1,
        "not_readable": 2,
        "recoverable": 1,
        "never_attempted": 2,
    }

    assert progress_sentence(progress) == (
        "7 of 13 Included file(s) fully analysed; 1 partly analysed; 3 not yet analysed; "
        "2 could not be read."
    )
    assert progress_sentence({}) == (
        "0 of 0 Included file(s) fully analysed; 0 partly analysed; 0 not yet analysed; "
        "0 could not be read."
    )


def test_job_view_offers_retry_only_when_it_can_help() -> None:
    rows = [_row("INVALID_MODEL_OUTPUT", passages_total=1, model_calls=1)]

    recoverable = discovery_job_view({"state": "PARTIAL"}, rows, retry_worthwhile=True)
    futile = discovery_job_view({"state": "FAILED"}, rows, retry_worthwhile=False)
    done = discovery_job_view({"state": "SUCCEEDED"}, rows, retry_worthwhile=True)
    empty = discovery_job_view({"state": "RUNNING"}, [], retry_worthwhile=True)

    assert (recoverable["offer_retry"], recoverable["no_retry_reason"]) == (True, "")
    assert (futile["offer_retry"], futile["no_retry_reason"]) == (False, NO_RETRY_REASON)
    assert (done["offer_retry"], done["no_retry_reason"]) == (False, "")
    assert (empty["files_sentence"], empty["passages_sentence"]) == ("", "")
    assert recoverable["state_label"] == "Analysis partly complete"
