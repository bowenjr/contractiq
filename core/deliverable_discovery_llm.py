"""OPS-13A — local-LLM candidate deliverable proposal generation.

The LLM never sees anything but bounded, already-extracted passages, and every proposal it
returns must cite one of the exact `extraction_id`s it was given. A citation to any other ID —
or a malformed response — is dropped, never silently accepted, and counted so the run can report
how much was rejected. Silence from the LLM (no proposal for a given unit) is never interpreted
as "not required"; it simply produces no candidate, leaving the underlying extraction unit
visible in the review queue's evidence trail regardless.
"""
# ruff: noqa: E501

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
from core.deliverable_discovery_extraction import VDDR_TABLE_KIND, RawUnit
from core.deliverable_discovery_requirements import legacy_criticality, parse_requirement
from core.requirement_lifecycle import (
    AppliesAt,
    ClassifiedBy,
    Confidence,
    DiscoveryLifecycle,
    ItemKind,
    ProposalClassification,
    RequirementStatus,
)
from core.vddr_table import interpret_vddr_unit

SYSTEM_PROMPT = """You analyse one passage from a customer RFP/tender package for a company that will quote equipment through a manufacturer. Find every distinct requirement the passage places on the bidder or its manufacturer that is a document, form, schedule, declaration, data input, test, record, deliverable or action. Classify WHEN each is due:

- PRE_BID_ACTION: something the bidder must do, ask, confirm or arrange BEFORE submitting the proposal (acknowledge the RFP, attend a meeting, submit a clarification, register).
- BID_RETURNABLE: a form, schedule, declaration, document or other item that must be submitted WITH the proposal.
- MANUFACTURER_INPUT: information or documents the bidder must obtain from the manufacturer/supplier to prepare or validate the quotation (technical data, completed datasheet fields, manufacturer price and delivery, supplier qualifications/exceptions).
- POST_AWARD: documentation, tests, drawings, records or activities required only after purchase order or contract award (engineering drawings, manufacturing and inspection documents, FAT records, final data books, warranties, bonds).
- CONDITIONAL: required or optional only under a stated circumstance; fill condition_text and applies_at (WITH_BID or POST_AWARD).
- NOT_APPLICABLE: template or boilerplate content the passage itself shows does not apply.
- NEEDS_REVIEW: an obligation exists but the passage does not make the timing clear; say why in review_reason.

requirement_status is REQUIRED, OPTIONAL, CONDITIONAL or UNCLEAR. item_kind is SUBMISSION_DOCUMENT (a form, schedule or document the bidder submits), MANUFACTURER_DOCUMENT (a document authored by the manufacturer), MANUFACTURER_DATA (data or prices the manufacturer supplies), COMMERCIAL_QUALIFICATION (an exception, qualification or clarification of commercial terms such as insurance, bonds, warranty, payment, liability), CLARIFICATION (a question or confirmation to be sent to the customer), ACTIVITY (a meeting, test, inspection or other action) or OTHER. For COMMERCIAL_QUALIFICATION put the closest topic key in category, one of: PRICE_BASIS, BID_VALIDITY, CURRENCY, FX_EXPOSURE, TAXES_DUTIES, FREIGHT_DELIVERY, INCOTERMS, PAYMENT_TERMS, BILLING_MILESTONES, HOLDBACK, BONDS_GUARANTEES, LETTERS_OF_CREDIT, INSURANCE, WARRANTY, LIQUIDATED_DAMAGES, LIMITATION_LIABILITY, INDEMNITY, TERMINATION, SUSPENSION, SCHEDULE, CUSTOMER_DELAY, CHANGE_MANAGEMENT, FORCE_MAJEURE, INTELLECTUAL_PROPERTY, CONFIDENTIALITY, GOVERNING_LAW, DISPUTE_RESOLUTION, ESCALATION, COMMODITY_EXPOSURE, SEPARATELY_CHARGEABLE, EXCLUSIONS, ASSUMPTIONS, QUALIFICATIONS; otherwise category is a short plain label such as Form, Schedule, Certificate, Drawing, Plan, Report, Test or Data.

Rules:
- A form, schedule or datasheet the bidder must complete is ONE requirement (BID_RETURNABLE); never list its individual rows, fields or questions as separate requirements.
- When completing such a form needs data that only the manufacturer can supply (technical or datasheet values, equipment price, delivery or lead time, test or quality certificates, supplier qualifications or exceptions), ALSO return one separate MANUFACTURER_INPUT requirement for each related group of that data (for example "Transformer technical datasheet values" or "Manufacturer price and delivery"), never one per field.
- If a passage has both a bid-stage part and a post-award part, return two separate requirements with the same title.
- Do not list general descriptions, definitions, customer obligations, technical performance limits or contract rights as requirements.
- Never invent a requirement, a responsible party, a date or a deadline. responsible_party is only a party the passage names, otherwise null.
- timing_text is the exact wording the passage uses for a date, time or timing rule (for example "3 Business Days Before RFP Closing Date"), copied word for word, otherwise null. Never write your own timing summary.
- due_date is null unless the passage prints an explicit calendar date (YYYY-MM-DD). Never calculate or guess a date.
- source_quote is the one sentence of the passage that creates the obligation, copied word for word (at most 300 characters), or null. Never paraphrase it.
- Use confidence LOW when you are unsure of the lifecycle; the item will be sent to a person for review.
- Return at most 10 requirements per passage and keep every text field under 160 characters. If the passage names no requirement return an empty list.
- Respond ONLY with JSON matching the schema. No explanation outside the JSON object."""

_LIFECYCLES = [item.value for item in DiscoveryLifecycle]
_NULLABLE_TEXT: dict[str, object] = {"type": ["string", "null"]}
_REQUIREMENT_FIELDS: dict[str, object] = {
    "title": {"type": "string"},
    "description": {"type": "string"},
    "lifecycle": {"type": "string", "enum": _LIFECYCLES},
    "requirement_status": {"type": "string", "enum": [item.value for item in RequirementStatus]},
    "item_kind": {"type": "string", "enum": [item.value for item in ItemKind]},
    "category": {"type": "string"},
    "condition_text": _NULLABLE_TEXT,
    "applies_at": {"type": ["string", "null"], "enum": [item.value for item in AppliesAt] + [None]},
    "responsible_party": _NULLABLE_TEXT,
    "timing_text": _NULLABLE_TEXT,
    "due_date": _NULLABLE_TEXT,
    "dependencies": _NULLABLE_TEXT,
    "confidence": {"type": "string", "enum": [item.value for item in Confidence]},
    "review_reason": _NULLABLE_TEXT,
    "source_section": _NULLABLE_TEXT,
    "source_quote": _NULLABLE_TEXT,
}
CANDIDATE_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _REQUIREMENT_FIELDS,
                "required": list(_REQUIREMENT_FIELDS),
                "additionalProperties": False,
            },
        }
    },
    "required": ["requirements"],
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
            "name": "requirements",
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
    due_date_text: str | None = Field(default=None, max_length=1000)
    source_quote: str | None = Field(default=None, max_length=2000)

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
    due_date_text: str | None = None
    source_quote: str | None = None
    description: str | None = None
    classification: ProposalClassification | None = None
    """Present for Stage 2 replies and rules; ``None`` for a legacy-shaped reply."""


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

    if _reply_items(raw) is None:
        return (
            None,
            "invalid",
            f"{INVALID_REASON_PREFIX}model reply did not contain a requirements list",
        )
    return raw, None, None


def _reply_items(raw: object) -> list[object] | None:
    """The reply's item list: ``requirements`` (Stage 2) or ``candidates`` (legacy shape)."""
    if not isinstance(raw, dict):
        return None
    for key in ("requirements", "candidates"):
        value = raw.get(key)
        if isinstance(value, list):
            return value
    return None


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
        if unit.kind == VDDR_TABLE_KIND:
            ruled = _items_from_rules(extraction_id, unit)
            if ruled is None:
                outcomes.append(
                    PassageOutcome(
                        extraction_id=extraction_id,
                        outcome=PassageOutcomeKind.INVALID_OUTPUT,
                        safe_reason=f"{INVALID_REASON_PREFIX}table rows could not be read",
                    )
                )
                continue
            candidates.extend(ruled)
            outcomes.append(
                PassageOutcome(
                    extraction_id=extraction_id,
                    outcome=PassageOutcomeKind.ANALYSED,
                    candidate_count=len(ruled),
                )
            )
            continue
        try:
            prompt = (
                f"Document: {unit.document or 'unknown'}\n"
                f"Passage ({unit.locator_type} {unit.locator}):\n\n{unit.text}"
            )
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

        raw_items = _reply_items(raw)
        if raw_items is None:
            outcomes.append(
                PassageOutcome(
                    extraction_id=extraction_id,
                    outcome=PassageOutcomeKind.INVALID_OUTPUT,
                    safe_reason=(
                        f"{INVALID_REASON_PREFIX}model reply did not contain a requirements list"
                    ),
                )
            )
            continue

        accepted = 0
        invalid = 0
        for item in raw_items:
            if not isinstance(item, dict):
                invalid += 1
                continue
            candidate = _candidate_from_item(extraction_id, unit.text, item)
            if candidate is None:
                invalid += 1
                continue
            candidates.append(candidate)
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


def _candidate_from_item(
    extraction_id: str,
    passage_text: str,
    item: dict[str, object],
    classified_by: ClassifiedBy = ClassifiedBy.MODEL,
) -> ProposedCandidate | None:
    """A candidate from one reply item, or ``None`` when the item is unusable.

    A Stage 2 item carries ``lifecycle``; anything else is a legacy-shaped item and yields an
    unclassified candidate that behaves exactly as it did before lifecycle classification.
    """
    if "lifecycle" in item:
        try:
            raw, classification = parse_requirement(item, passage_text, classified_by)
        except (ValidationError, ValueError, KeyError):
            return None
        return ProposedCandidate(
            extraction_id=extraction_id,
            title=raw.title,
            category=raw.category,
            criticality=legacy_criticality(raw.requirement_status, raw.condition_text),
            condition_text=raw.condition_text,
            due_date=raw.due_date,
            requested_format=None,
            requested_recipient=None,
            proposed_owner=None,
            due_date_text=raw.timing_text,
            source_quote=raw.source_quote,
            description=classification.description or None,
            classification=classification,
        )
    try:
        parsed = RawCandidate.model_validate(item)
    except ValidationError:
        return None
    due_date = _validate_date(parsed.due_date)
    if parsed.due_date and due_date is None:
        return None
    return ProposedCandidate(
        extraction_id=extraction_id,
        title=parsed.title,
        category=parsed.category,
        criticality=parsed.criticality,
        condition_text=parsed.condition_text,
        due_date=due_date,
        requested_format=parsed.requested_format,
        requested_recipient=parsed.requested_recipient,
        proposed_owner=parsed.proposed_owner,
        due_date_text=parsed.due_date_text,
        source_quote=parsed.source_quote,
    )


def _items_from_rules(extraction_id: str, unit: RawUnit) -> list[ProposedCandidate] | None:
    """Findings read by rules from rendered table rows, or ``None`` if the rows are unreadable."""
    try:
        items = interpret_vddr_unit(unit.text)
    except ValueError:
        return None
    candidates = [
        _candidate_from_item(extraction_id, unit.text, item, ClassifiedBy.RULE) for item in items
    ]
    if any(candidate is None for candidate in candidates):
        return None
    return [candidate for candidate in candidates if candidate is not None]


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
