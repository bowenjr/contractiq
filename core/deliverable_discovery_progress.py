"""Current-source discovery progress derived from immutable per-run file outcomes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.deliverable_discovery_outcomes import FileOutcomeKind


class DiscoverySourceProgress(BaseModel):
    """Discovery coverage for the files that are Included now."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fully_analysed: int = Field(ge=0)
    partly_analysed: int = Field(ge=0)
    not_readable: int = Field(ge=0)
    recoverable: int = Field(ge=0)
    never_attempted: int = Field(ge=0)
    passages_unanalysed: int = Field(ge=0)
    has_results: bool
    retry_worthwhile: bool
    effective_state: Literal["SUCCEEDED", "PARTIAL", "FAILED"] | None


def discovery_source_progress(
    included_file_ids: Iterable[str],
    file_outcomes: Iterable[Mapping[str, object]],
) -> DiscoverySourceProgress:
    """Reduce all run outcomes to the best result for every currently Included file."""
    included = set(included_file_ids)
    chosen: dict[
        str,
        tuple[FileOutcomeKind, int, int, tuple[str, int, int]],
    ] = {}
    fully_analysed_outcomes = {
        FileOutcomeKind.ANALYSED_CANDIDATES,
        FileOutcomeKind.ANALYSED_ZERO_CANDIDATES,
    }
    for position, row in enumerate(file_outcomes):
        file_id = str(row["received_file_id"])
        if file_id not in included:
            continue
        outcome = FileOutcomeKind(str(row["outcome"]))
        passages_total = max(0, int(str(row["passages_total"])))
        passages_analysed = max(0, int(str(row["passages_analysed"])))
        completed_at = str(row.get("run_completed_at") or row.get("completed_at") or "")
        run_order = int(str(row.get("run_order") or 0))
        recency = (completed_at, run_order, position)
        candidate = (outcome, passages_total, passages_analysed, recency)
        current = chosen.get(file_id)
        if current is None:
            chosen[file_id] = candidate
            continue
        candidate_full = outcome in fully_analysed_outcomes
        current_full = current[0] in fully_analysed_outcomes
        if candidate_full != current_full:
            if candidate_full:
                chosen[file_id] = candidate
            continue
        if candidate_full:
            if recency > current[3]:
                chosen[file_id] = candidate
            continue
        if passages_analysed > current[2] or (
            passages_analysed == current[2] and recency > current[3]
        ):
            chosen[file_id] = candidate

    fully_analysed = 0
    partly_analysed = 0
    not_readable = 0
    recoverable = 0
    passages_unanalysed = 0
    not_readable_outcomes = {
        FileOutcomeKind.UNSUPPORTED_TYPE,
        FileOutcomeKind.UNREADABLE,
        FileOutcomeKind.EXTRACTED_NO_TEXT,
    }
    recoverable_outcomes = {
        FileOutcomeKind.MODEL_UNAVAILABLE,
        FileOutcomeKind.INVALID_MODEL_OUTPUT,
        FileOutcomeKind.NOT_ATTEMPTED,
        FileOutcomeKind.PARTIALLY_ANALYSED,
    }
    for outcome, passages_total, passages_analysed, _recency in chosen.values():
        if outcome in fully_analysed_outcomes:
            fully_analysed += 1
        elif passages_analysed > 0:
            partly_analysed += 1
            passages_unanalysed += max(0, passages_total - passages_analysed)
        elif outcome in not_readable_outcomes:
            not_readable += 1
        elif outcome in recoverable_outcomes:
            recoverable += 1
            passages_unanalysed += max(0, passages_total - passages_analysed)

    never_attempted = len(included - chosen.keys())
    has_results = bool(chosen)
    retry_worthwhile = partly_analysed + recoverable + never_attempted > 0
    if not has_results:
        effective_state: Literal["SUCCEEDED", "PARTIAL", "FAILED"] | None = None
    elif fully_analysed == len(included):
        effective_state = "SUCCEEDED"
    elif fully_analysed or partly_analysed:
        effective_state = "PARTIAL"
    else:
        effective_state = "FAILED"
    return DiscoverySourceProgress(
        fully_analysed=fully_analysed,
        partly_analysed=partly_analysed,
        not_readable=not_readable,
        recoverable=recoverable,
        never_attempted=never_attempted,
        passages_unanalysed=passages_unanalysed,
        has_results=has_results,
        retry_worthwhile=retry_worthwhile,
        effective_state=effective_state,
    )
