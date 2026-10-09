"""Pure presentation facts for discovery extraction and model coverage."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.deliverable_discovery_extraction import (
    UnsupportedMediaTypeError,
    locator_type_for_filename,
)

UNSUPPORTED_FILE_REASON = "file type not supported for discovery (PDF, Word or Excel only)"
_FULLY_ANALYSED = {"ANALYSED_CANDIDATES", "ANALYSED_ZERO_CANDIDATES"}


def discovery_coverage(
    files: Sequence[Mapping[str, object]],
    latest_run: Mapping[str, object] | None,
    processed_file_ids: set[str],
    *,
    unreadable_count: int,
) -> dict[str, object]:
    """Summarize extraction separately from completed model analysis."""
    included = [row for row in files if row.get("analysis_eligibility") == "ELIGIBLE"]
    excluded_count = sum(row.get("analysis_eligibility") == "EXCLUDED" for row in files)
    undecided_count = len(files) - len(included) - excluded_count
    has_run = latest_run is not None
    outcome = str(latest_run["outcome"]) if latest_run and latest_run.get("outcome") else None
    file_outcomes = list(latest_run.get("file_outcomes") or []) if latest_run else []
    passages_total = int(latest_run.get("passages_total") or 0) if latest_run else 0
    model_calls = int(latest_run.get("model_calls") or 0) if latest_run else 0
    passages_not_attempted = max(0, passages_total - model_calls)

    not_fully_analysed: list[dict[str, str]] = []
    if file_outcomes:
        for item in file_outcomes:
            if str(item["outcome"]) in _FULLY_ANALYSED:
                continue
            not_fully_analysed.append(
                {
                    "name": str(item.get("original_filename") or item["received_file_id"]),
                    "outcome": str(item["outcome"]),
                    "detail": str(item["detail"]),
                }
            )
        extracted_count = int(latest_run.get("files_extracted") or 0)
        analysed_count = int(latest_run.get("files_analysed") or 0)
        run_included_count = int(latest_run.get("files_included") or len(file_outcomes))
        not_analysed_count = run_included_count - analysed_count
    elif has_run:
        # Extraction rows from a pre-v4 run are not proof that the model analysed a file.
        run_included_count = len(included)
        included_ids = {str(row["file_id"]) for row in included}
        extracted_count = len(included_ids & processed_file_ids)
        # Whether the model analysed anything in a pre-v4 run was never recorded: unknown, not 0.
        analysed_count = None
        not_analysed_count = None
        for row in included:
            filename = str(row.get("original_filename") or row.get("original_relative_path") or "")
            try:
                locator_type_for_filename(filename)
            except UnsupportedMediaTypeError:
                detail = UNSUPPORTED_FILE_REASON
            else:
                detail = "recorded before per-file outcomes were kept"
            not_fully_analysed.append({"name": filename, "outcome": "LEGACY", "detail": detail})
    else:
        run_included_count = len(included)
        extracted_count = 0
        analysed_count = 0
        not_analysed_count = len(included)

    proposal_count = int(latest_run["proposal_count"]) if latest_run is not None else 0
    rejected_source_count = (
        int(latest_run["rejected_source_count"]) if latest_run is not None else 0
    )
    honesty_reasons: list[str] = []
    if outcome in {"PARTIAL", "FAILED"}:
        honesty_reasons.extend(
            f"{item['name']} — {item['outcome']}: {item['detail']}" for item in not_fully_analysed
        )
    elif has_run and outcome is None:
        honesty_reasons.append("this run was recorded before per-file outcomes were kept")

    return {
        "included_count": run_included_count if has_run else len(included),
        "excluded_count": excluded_count,
        "undecided_count": undecided_count,
        "has_run": has_run,
        "latest_run": dict(latest_run) if latest_run is not None else None,
        "outcome": outcome,
        "outcome_label": outcome or "LEGACY",
        "legacy": has_run and outcome is None,
        "extracted_count": extracted_count,
        "analysed_count": analysed_count,
        "not_analysed_count": not_analysed_count,
        "not_fully_analysed_files": not_fully_analysed,
        "processed_count": extracted_count,
        "unprocessed_count": not_analysed_count or 0,
        "unprocessed_files": not_fully_analysed,
        "proposal_count": proposal_count,
        "rejected_source_count": rejected_source_count,
        "honesty_warning": proposal_count == 0 and bool(honesty_reasons),
        "honesty_reasons": honesty_reasons,
        "unreadable_count": unreadable_count,
        "passages_total": passages_total,
        "model_calls": model_calls,
        "passages_not_attempted": passages_not_attempted,
    }


__all__ = ["UNSUPPORTED_FILE_REASON", "discovery_coverage"]
