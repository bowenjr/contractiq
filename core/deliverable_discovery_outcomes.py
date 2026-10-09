"""Deterministic OPS-13A discovery run and file outcome models.

File outcomes are exhaustive for every Included file: ``UNSUPPORTED_TYPE`` cannot be
extracted; ``UNREADABLE`` means every extracted unit failed (including managed-copy
failures); ``EXTRACTED_NO_TEXT`` had no non-blank passage to send; ``NOT_ATTEMPTED`` means
no readable passage reached the model before a job stopped; ``MODEL_UNAVAILABLE`` means
every passage that actually reached the model had a transport/model failure;
``INVALID_MODEL_OUTPUT`` means every attempted passage returned unusable output;
``PARTIALLY_ANALYSED`` means only some content was analysed; and the two ``ANALYSED_*``
outcomes distinguish successful analysis with candidates from successful analysis with a
valid zero-candidate finding. Unattempted passages are derived from total passages minus
real model calls and are never persisted as job-passage attempts.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from core.deliverable_discovery_llm import PassageOutcome, PassageOutcomeKind


class DiscoveryOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class FileOutcomeKind(StrEnum):
    UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"
    UNREADABLE = "UNREADABLE"
    EXTRACTED_NO_TEXT = "EXTRACTED_NO_TEXT"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    INVALID_MODEL_OUTPUT = "INVALID_MODEL_OUTPUT"
    PARTIALLY_ANALYSED = "PARTIALLY_ANALYSED"
    ANALYSED_CANDIDATES = "ANALYSED_CANDIDATES"
    ANALYSED_ZERO_CANDIDATES = "ANALYSED_ZERO_CANDIDATES"


class DiscoveryFileOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    received_file_id: str
    outcome: FileOutcomeKind
    passages_total: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    passages_analysed: int = Field(ge=0)
    passages_model_failed: int = Field(ge=0)
    passages_invalid_output: int = Field(ge=0)
    detail: str = Field(min_length=1, max_length=1000)

    @property
    def passages_not_attempted(self) -> int:
        return max(0, self.passages_total - self.model_calls)


class DiscoveryRunOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    outcome: DiscoveryOutcome
    files_included: int = Field(ge=0)
    files_unsupported: int = Field(ge=0)
    files_unreadable: int = Field(ge=0)
    files_extracted: int = Field(ge=0)
    files_analysed: int = Field(ge=0)
    files_failed: int = Field(ge=0)
    passages_total: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    passages_analysed: int = Field(ge=0)
    passages_model_failed: int = Field(ge=0)
    passages_invalid_output: int = Field(ge=0)

    @property
    def passages_not_attempted(self) -> int:
        return max(0, self.passages_total - self.model_calls)


class FileAnalysisEvidence(BaseModel):
    """The extraction facts needed to derive one Included file's outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    received_file_id: str
    filename: str
    supported: bool = True
    extraction_completed: bool = False
    passage_ids: tuple[str, ...] = ()
    unreadable_reasons: tuple[str, ...] = ()


def _detail(parts: list[str]) -> str:
    return "; ".join(part for part in parts if part)[:1000]


def derive_discovery_outcomes(
    run_id: str,
    files: list[FileAnalysisEvidence],
    passage_outcomes: tuple[PassageOutcome, ...],
) -> tuple[DiscoveryRunOutcome, list[DiscoveryFileOutcome]]:
    """Derive honest run/file classifications from extraction and model facts."""
    by_extraction = {item.extraction_id: item for item in passage_outcomes}
    file_outcomes: list[DiscoveryFileOutcome] = []
    for file in files:
        reported = [by_extraction[item] for item in file.passage_ids if item in by_extraction]
        attempts = [
            item for item in reported if item.outcome is not PassageOutcomeKind.NOT_ATTEMPTED
        ]
        not_attempted = len(file.passage_ids) - len(attempts)
        analysed = sum(item.outcome is PassageOutcomeKind.ANALYSED for item in attempts)
        model_failed = sum(item.outcome is PassageOutcomeKind.MODEL_FAILURE for item in attempts)
        invalid_output = sum(item.outcome is PassageOutcomeKind.INVALID_OUTPUT for item in attempts)
        candidates = sum(item.candidate_count for item in attempts)
        invalid_candidates = sum(item.invalid_candidate_count for item in attempts)
        reasons = list(dict.fromkeys(item.safe_reason for item in attempts if item.safe_reason))

        if not file.supported:
            kind = FileOutcomeKind.UNSUPPORTED_TYPE
            detail = "file type not supported for discovery (PDF, Word or Excel only)"
        elif not file.passage_ids and file.unreadable_reasons:
            kind = FileOutcomeKind.UNREADABLE
            detail = _detail(["No readable passages were extracted", *file.unreadable_reasons])
        elif not file.passage_ids:
            kind = FileOutcomeKind.EXTRACTED_NO_TEXT
            detail = "The file was read, but it contained no non-blank passage to analyse."
        elif not attempts:
            kind = FileOutcomeKind.NOT_ATTEMPTED
            detail = f"None of the {not_attempted} readable passage(s) were attempted."
        elif analysed == len(file.passage_ids) and not file.unreadable_reasons:
            kind = (
                FileOutcomeKind.ANALYSED_CANDIDATES
                if candidates
                else FileOutcomeKind.ANALYSED_ZERO_CANDIDATES
            )
            detail = f"All {analysed} passage(s) were analysed by the model"
            if candidates:
                detail += f"; {candidates} candidate item(s) returned"
            else:
                detail += "; the model returned zero candidates"
            if invalid_candidates:
                detail += f"; {invalid_candidates} invalid candidate item(s) discarded"
        elif analysed:
            kind = FileOutcomeKind.PARTIALLY_ANALYSED
            detail = _detail(
                [
                    f"{analysed} of {len(file.passage_ids)} passage(s) were analysed",
                    f"{model_failed} model failure(s)" if model_failed else "",
                    f"{invalid_output} invalid model output(s)" if invalid_output else "",
                    f"{not_attempted} passage(s) not attempted" if not_attempted else "",
                    *file.unreadable_reasons,
                    *reasons,
                ]
            )
        elif model_failed == len(attempts):
            kind = FileOutcomeKind.MODEL_UNAVAILABLE
            detail = _detail(
                [
                    f"The model failed for all {model_failed} attempted passage(s)",
                    f"{not_attempted} not attempted" if not_attempted else "",
                    *reasons,
                ]
            )
        elif invalid_output == len(attempts):
            kind = FileOutcomeKind.INVALID_MODEL_OUTPUT
            detail = _detail(
                [
                    f"The model returned unusable output for all {invalid_output} passage(s)",
                    f"{not_attempted} passage(s) not attempted" if not_attempted else "",
                    *reasons,
                ]
            )
        else:
            kind = FileOutcomeKind.PARTIALLY_ANALYSED
            detail = _detail(
                [
                    "No passage completed analysis; failure types were mixed",
                    f"{model_failed} model failure(s)" if model_failed else "",
                    f"{invalid_output} invalid model output(s)" if invalid_output else "",
                    f"{not_attempted} passage(s) not attempted" if not_attempted else "",
                    *reasons,
                ]
            )

        file_outcomes.append(
            DiscoveryFileOutcome(
                run_id=run_id,
                received_file_id=file.received_file_id,
                outcome=kind,
                passages_total=len(file.passage_ids),
                model_calls=len(attempts),
                passages_analysed=analysed,
                passages_model_failed=model_failed,
                passages_invalid_output=invalid_output,
                detail=detail,
            )
        )

    fully_analysed = {
        FileOutcomeKind.ANALYSED_CANDIDATES,
        FileOutcomeKind.ANALYSED_ZERO_CANDIDATES,
    }
    files_analysed = sum(item.outcome in fully_analysed for item in file_outcomes)
    passages_analysed = sum(item.passages_analysed for item in file_outcomes)
    files_failed = len(file_outcomes) - files_analysed
    if passages_analysed == 0:
        outcome = DiscoveryOutcome.FAILED
    elif files_failed:
        outcome = DiscoveryOutcome.PARTIAL
    else:
        outcome = DiscoveryOutcome.SUCCEEDED
    run_outcome = DiscoveryRunOutcome(
        run_id=run_id,
        outcome=outcome,
        files_included=len(file_outcomes),
        files_unsupported=sum(
            item.outcome is FileOutcomeKind.UNSUPPORTED_TYPE for item in file_outcomes
        ),
        files_unreadable=sum(item.outcome is FileOutcomeKind.UNREADABLE for item in file_outcomes),
        files_extracted=sum(
            item.outcome not in {FileOutcomeKind.UNSUPPORTED_TYPE, FileOutcomeKind.UNREADABLE}
            for item in file_outcomes
        ),
        files_analysed=files_analysed,
        files_failed=files_failed,
        passages_total=sum(item.passages_total for item in file_outcomes),
        model_calls=sum(item.model_calls for item in file_outcomes),
        passages_analysed=passages_analysed,
        passages_model_failed=sum(item.passages_model_failed for item in file_outcomes),
        passages_invalid_output=sum(item.passages_invalid_output for item in file_outcomes),
    )
    return run_outcome, file_outcomes


__all__ = [
    "DiscoveryFileOutcome",
    "DiscoveryOutcome",
    "DiscoveryRunOutcome",
    "FileOutcomeKind",
    "FileAnalysisEvidence",
    "derive_discovery_outcomes",
]
