"""OPS-13A — local-LLM candidate deliverable proposal generation.

The LLM never sees anything but bounded, already-extracted passages, and every proposal it
returns must cite one of the exact `extraction_id`s it was given. A citation to any other ID —
or a malformed response — is dropped, never silently accepted, and counted so the run can report
how much was rejected. Silence from the LLM (no proposal for a given unit) is never interpreted
as "not required"; it simply produces no candidate, leaving the underlying extraction unit
visible in the review queue's evidence trail regardless.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from core.deliverable_discovery import DiscoveryCriticality
from core.deliverable_discovery_extraction import RawUnit

SYSTEM_PROMPT = """You review a passage from a customer Bid document and identify any deliverable, \
form, or returnable item the customer's submission requires (e.g. a signed bid form, a \
technical schedule, a pricing schedule, a certificate). Respond ONLY with JSON matching exactly:

{"candidates": [{"title": str, "category": str, "criticality": "REQUIRED"|"CONDITIONAL"|"UNCLEAR", \
"condition_text": str|null, "due_date": "YYYY-MM-DD"|null, "requested_format": str|null, \
"requested_recipient": str|null, "proposed_owner": str|null}]}

Rules:
- "candidates" may be an empty list if the passage names no deliverable.
- Use "CONDITIONAL" only when the passage states a condition, and always include condition_text.
- Use "UNCLEAR" when the passage implies an obligation but its requiredness is ambiguous.
- due_date is null unless the passage states an explicit date. Never guess a date.
- Never invent a deliverable not actually named or clearly implied in the passage text.
- Do not include any field, explanation, or text outside this JSON object."""

CANDIDATE_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "category": {"type": "string"},
                    "criticality": {
                        "type": "string",
                        "enum": ["REQUIRED", "CONDITIONAL", "UNCLEAR"],
                    },
                    "condition_text": {"type": ["string", "null"]},
                    "due_date": {"type": ["string", "null"]},
                    "requested_format": {"type": ["string", "null"]},
                    "requested_recipient": {"type": ["string", "null"]},
                    "proposed_owner": {"type": ["string", "null"]},
                },
                "required": [
                    "title",
                    "category",
                    "criticality",
                    "condition_text",
                    "due_date",
                    "requested_format",
                    "requested_recipient",
                    "proposed_owner",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}

DISCOVERY_MAX_TOKENS = 4096
DISCOVERY_RETRY_MAX_TOKENS = 8192
TRUNCATED_REASON_PREFIX = "truncated: "
INVALID_REASON_PREFIX = "invalid: "
CORRECTIVE_INSTRUCTION = (
    "Your previous reply could not be used. Reply again with ONLY the JSON object "
    "described above, complete and valid, and nothing else."
)
DISCOVERY_REQUEST_OPTIONS: dict[str, object] = {
    "chat_template_kwargs": {"enable_thinking": False},
    "response_format": {
        "type": "json_schema",
        "json_schema": {
            "name": "candidates",
            "strict": True,
            "schema": CANDIDATE_RESPONSE_SCHEMA,
        },
    },
}


class LLMClientProtocol(Protocol):
    def complete_json(
        self,
        prompt: str,
        system_prompt: str = ...,
        temperature: float = ...,
        max_tokens: int = ...,
        context_label: str | None = ...,
        timeout: int | None = ...,
    ) -> dict[str, object]: ...


class RawCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    category: str = Field(min_length=1, max_length=100)
    criticality: DiscoveryCriticality
    condition_text: str | None = None
    due_date: str | None = None
    requested_format: str | None = None
    requested_recipient: str | None = None
    proposed_owner: str | None = None

    @model_validator(mode="after")
    def conditional_requires_condition(self) -> RawCandidate:
        if self.criticality is DiscoveryCriticality.CONDITIONAL and not self.condition_text:
            raise ValueError("conditional candidate requires condition_text")
        return self


class ProposedCandidate(BaseModel):
    """A schema-valid candidate tied back to the exact extraction unit that produced it."""

    model_config = ConfigDict(extra="forbid")

    extraction_id: str
    title: str
    category: str
    criticality: DiscoveryCriticality
    condition_text: str | None
    due_date: str | None
    requested_format: str | None
    requested_recipient: str | None
    proposed_owner: str | None


class PassageOutcomeKind(StrEnum):
    ANALYSED = "ANALYSED"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    MODEL_FAILURE = "MODEL_FAILURE"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


@dataclass(frozen=True)
class PassageOutcome:
    extraction_id: str
    outcome: PassageOutcomeKind
    candidate_count: int = 0
    invalid_candidate_count: int = 0
    safe_reason: str | None = None
    transport_failure: bool = False


@dataclass(frozen=True)
class AnalysisResult:
    candidates: tuple[ProposedCandidate, ...]
    outcomes: tuple[PassageOutcome, ...]

    @property
    def invalid_count(self) -> int:
        return sum(
            item.invalid_candidate_count
            + (1 if item.outcome is PassageOutcomeKind.INVALID_OUTPUT else 0)
            for item in self.outcomes
        )


_MODEL_FAILURES = (
    ConnectionError,
    TimeoutError,
    RuntimeError,
    OSError,
    requests.exceptions.RequestException,
)


def _safe_reason(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {message}"[:300]


def is_truncated_reason(reason: str | None) -> bool:
    """Return whether a safe reason identifies model-output truncation."""
    return reason is not None and reason.startswith(TRUNCATED_REASON_PREFIX)


def _attempt_completion(
    llm: LLMClientProtocol,
    *,
    prompt: str,
    system_prompt: str,
    allowance: int,
    context_label: str,
    timeout: int | None,
) -> tuple[dict[str, object] | None, str | None, str | None]:
    structured_completion = getattr(llm, "complete_structured", None)
    if callable(structured_completion):
        call_options: dict[str, object] = {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "temperature": 0.05,
            "max_tokens": allowance,
            "extra_body": DISCOVERY_REQUEST_OPTIONS,
            "context_label": context_label,
        }
        if timeout is not None:
            call_options["timeout"] = timeout
        reply = structured_completion(**call_options)
        if reply.finish_reason == "length":
            return (
                None,
                "truncated",
                f"{TRUNCATED_REASON_PREFIX}the model reply reached the "
                f"{allowance}-token output limit before the answer was complete",
            )
        text = reply.content
        start = text.find("{")
        end = text.rfind("}")
        try:
            raw = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return (
                None,
                "invalid",
                f"{INVALID_REASON_PREFIX}the model reply was not valid JSON",
            )
        if not isinstance(raw, dict):
            return (
                None,
                "invalid",
                f"{INVALID_REASON_PREFIX}the model reply was not valid JSON",
            )
    else:
        legacy_options: dict[str, object] = {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "temperature": 0.05,
            "max_tokens": 1024,
            "context_label": context_label,
        }
        if timeout is not None:
            legacy_options["timeout"] = timeout
        try:
            raw = llm.complete_json(
                **legacy_options,  # type: ignore[arg-type]
            )
        except json.JSONDecodeError:
            return (
                None,
                "invalid",
                f"{INVALID_REASON_PREFIX}the model reply was not valid JSON",
            )

    raw_candidates = raw.get("candidates") if isinstance(raw, dict) else None
    if not isinstance(raw_candidates, list):
        return (
            None,
            "invalid",
            f"{INVALID_REASON_PREFIX}model reply did not contain a candidates list",
        )
    return raw, None, None


def analyse_units(
    llm: LLMClientProtocol,
    units: list[tuple[str, RawUnit]],
    *,
    timeout: int | None = None,
) -> AnalysisResult:
    """Classify every model attempt without conflating failures with invalid output."""
    candidates: list[ProposedCandidate] = []
    outcomes: list[PassageOutcome] = []
    for extraction_id, unit in units:
        if not unit.readable or not unit.text.strip():
            continue
        try:
            prompt = f"Passage ({unit.locator_type} {unit.locator}):\n\n{unit.text}"
            context_label = f"deliverable-discovery:{unit.locator}"
            started_at = time.monotonic()
            raw, problem, reason = _attempt_completion(
                llm,
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                allowance=DISCOVERY_MAX_TOKENS,
                context_label=context_label,
                timeout=timeout,
            )
            if problem is not None:
                retry_timeout = None
                if timeout is not None:
                    elapsed_seconds = time.monotonic() - started_at
                    retry_timeout = max(60, timeout - int(elapsed_seconds))
                retry_allowance = (
                    DISCOVERY_RETRY_MAX_TOKENS if problem == "truncated" else DISCOVERY_MAX_TOKENS
                )
                raw, problem, reason = _attempt_completion(
                    llm,
                    prompt=prompt,
                    system_prompt=f"{SYSTEM_PROMPT}\n\n{CORRECTIVE_INSTRUCTION}",
                    allowance=retry_allowance,
                    context_label=context_label,
                    timeout=retry_timeout,
                )
        except _MODEL_FAILURES as exc:
            outcomes.append(
                PassageOutcome(
                    extraction_id=extraction_id,
                    outcome=PassageOutcomeKind.MODEL_FAILURE,
                    safe_reason=_safe_reason(exc),
                    transport_failure=isinstance(
                        exc,
                        (
                            ConnectionError,
                            TimeoutError,
                            requests.exceptions.ConnectionError,
                            requests.exceptions.Timeout,
                        ),
                    ),
                )
            )
            continue
        except Exception as exc:
            outcomes.append(
                PassageOutcome(
                    extraction_id=extraction_id,
                    outcome=PassageOutcomeKind.INVALID_OUTPUT,
                    safe_reason=_safe_reason(exc),
                )
            )
            continue

        if problem is not None:
            outcomes.append(
                PassageOutcome(
                    extraction_id=extraction_id,
                    outcome=PassageOutcomeKind.INVALID_OUTPUT,
                    safe_reason=f"{reason} (after one retry)",
                )
            )
            continue

        raw_candidates = raw.get("candidates") if isinstance(raw, dict) else None
        if not isinstance(raw_candidates, list):
            outcomes.append(
                PassageOutcome(
                    extraction_id=extraction_id,
                    outcome=PassageOutcomeKind.INVALID_OUTPUT,
                    safe_reason=(
                        f"{INVALID_REASON_PREFIX}model reply did not contain a candidates list"
                    ),
                )
            )
            continue

        accepted = 0
        invalid = 0
        for item in raw_candidates:
            if not isinstance(item, dict):
                invalid += 1
                continue
            try:
                parsed = RawCandidate.model_validate(item)
            except ValidationError:
                invalid += 1
                continue
            due_date = _validate_date(parsed.due_date)
            if parsed.due_date and due_date is None:
                invalid += 1
                continue
            candidates.append(
                ProposedCandidate(
                    extraction_id=extraction_id,
                    title=parsed.title,
                    category=parsed.category,
                    criticality=parsed.criticality,
                    condition_text=parsed.condition_text,
                    due_date=due_date,
                    requested_format=parsed.requested_format,
                    requested_recipient=parsed.requested_recipient,
                    proposed_owner=parsed.proposed_owner,
                )
            )
            accepted += 1
        outcomes.append(
            PassageOutcome(
                extraction_id=extraction_id,
                outcome=PassageOutcomeKind.ANALYSED,
                candidate_count=accepted,
                invalid_candidate_count=invalid,
            )
        )
    return AnalysisResult(candidates=tuple(candidates), outcomes=tuple(outcomes))


def propose_candidates(
    llm: LLMClientProtocol,
    units: list[tuple[str, RawUnit]],
) -> tuple[list[ProposedCandidate], int]:
    """Run one LLM call per readable unit. Returns (candidates, rejected_count).

    `units` is a list of (extraction_id, RawUnit) pairs — only units with `readable=True`
    should be passed in; the caller is responsible for filtering UNREADABLE units out, since
    there is nothing for the model to read.
    """
    result = analyse_units(llm, units)
    return list(result.candidates), result.invalid_count


def _validate_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None
    return value
