"""Acceptance regressions for Stage 2 lifecycle guards and model compatibility."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.deliverable_discovery_extraction import RawUnit
from core.deliverable_discovery_llm import (
    DISCOVERY_MAX_TOKENS,
    DISCOVERY_RETRY_MAX_TOKENS,
    SYSTEM_PROMPT,
    ProposedCandidate,
    _attempt_completion,
    analyse_units,
)
from core.llm_client import StructuredReply
from core.requirement_lifecycle import DiscoveryLifecycle

OPTION_REVIEW_REASON = (
    'The passage gives the bidder an option ("may"), not an obligation. Reject it, or move '
    "it if it is a real requirement."
)
BIDDER_INFORMATION_REVIEW_REASON = (
    "This describes the bidder's own information (for example its insurance), not data held "
    "by the manufacturer. Move it to With the bid, or reject it if the form already covers it."
)
OPTION_PASSAGE = (
    "A Bidder who has submitted a Proposal may withdraw its Proposal at any time prior to the "
    "Closing Date and Time by giving written notice to that effect to the Sole Point of Contact."
)
INSURANCE_PASSAGE = (
    "Schedule C - Insurance. The Bidder must complete the following information: Workers' "
    "Compensation Insurance — Insurer: [XX], Policy No.: [XX], Expiry Date: [XX] ..."
)


def _unit(text: str) -> RawUnit:
    return RawUnit(locator_type="PAGE", locator="p.1", text=text, readable=True)


def _requirement(passage: str, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "title": "Synthetic requirement",
        "description": "A synthetic requirement for classification.",
        "lifecycle": "BID_RETURNABLE",
        "requirement_status": "REQUIRED",
        "item_kind": "SUBMISSION_DOCUMENT",
        "category": "Form",
        "condition_text": None,
        "applies_at": None,
        "responsible_party": None,
        "timing_text": None,
        "due_date": None,
        "dependencies": None,
        "confidence": "HIGH",
        "review_reason": None,
        "source_section": None,
        "source_quote": passage,
    }
    item.update(overrides)
    return item


class _LegacyScript:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.max_tokens: list[int] = []

    def complete_json(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.05,
        max_tokens: int = 4096,
        context_label: str | None = None,
        timeout: int | None = None,
    ) -> dict[str, object]:
        del prompt, system_prompt, temperature, context_label, timeout
        self.max_tokens.append(max_tokens)
        return self.responses.pop(0)


class _StructuredScript:
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents

    def complete_structured(self, **_: object) -> StructuredReply:
        return StructuredReply(content=self.contents.pop(0), finish_reason="stop")

    def complete_json(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.05,
        max_tokens: int = 4096,
        context_label: str | None = None,
        timeout: int | None = None,
    ) -> dict[str, object]:
        del prompt, system_prompt, temperature, max_tokens, context_label, timeout
        raise AssertionError("structured completion should be used")


def _classify(passage: str, item: dict[str, Any]) -> ProposedCandidate:
    result = analyse_units(_LegacyScript([{"requirements": [item]}]), [("E-1", _unit(passage))])
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.classification is not None
    return candidate


def test_bidder_option_is_retained_for_review_with_model_lifecycle_and_quote() -> None:
    candidate = _classify(
        OPTION_PASSAGE,
        _requirement(
            OPTION_PASSAGE,
            title="Proposal Withdrawal",
            description="Written notice used to withdraw a submitted proposal.",
            lifecycle="PRE_BID_ACTION",
            item_kind="ACTIVITY",
        ),
    )

    classification = candidate.classification
    assert classification is not None
    assert classification.lifecycle is DiscoveryLifecycle.NEEDS_REVIEW
    assert classification.model_lifecycle is DiscoveryLifecycle.PRE_BID_ACTION
    assert classification.confidence.value == "HIGH"
    assert classification.review_reason == OPTION_REVIEW_REASON
    assert candidate.source_quote == OPTION_PASSAGE


def test_bidder_insurance_data_is_retained_for_review_with_model_lifecycle_and_quote() -> None:
    candidate = _classify(
        INSURANCE_PASSAGE,
        _requirement(
            INSURANCE_PASSAGE,
            title="Insurance Policy Details",
            description=(
                "Insurer names, policy numbers, and expiry dates for all required insurance types"
            ),
            lifecycle="MANUFACTURER_INPUT",
            item_kind="MANUFACTURER_DATA",
        ),
    )

    classification = candidate.classification
    assert classification is not None
    assert classification.lifecycle is DiscoveryLifecycle.NEEDS_REVIEW
    assert classification.model_lifecycle is DiscoveryLifecycle.MANUFACTURER_INPUT
    assert classification.confidence.value == "HIGH"
    assert classification.review_reason == BIDDER_INFORMATION_REVIEW_REASON
    assert candidate.source_quote == INSURANCE_PASSAGE


@pytest.mark.parametrize(
    ("passage", "lifecycle", "title", "description", "item_kind"),
    [
        (
            "Bidder must submit written notice of intent to bid by 2026-10-01.",
            "PRE_BID_ACTION",
            "Notice of intent",
            "Submit a notice of intent.",
            "ACTIVITY",
        ),
        (
            "The Bidder may be required to attend a pre-bid meeting.",
            "PRE_BID_ACTION",
            "Pre-bid meeting",
            "Attend the pre-bid meeting if required.",
            "ACTIVITY",
        ),
        (
            "Bidders may submit questions before closing. All questions shall be in writing.",
            "PRE_BID_ACTION",
            "Written questions",
            "Submit questions in writing.",
            "CLARIFICATION",
        ),
        (
            "The Bidder must obtain Manufacturer price and delivery data.",
            "MANUFACTURER_INPUT",
            "Manufacturer price and delivery data",
            "Manufacturer price and delivery data.",
            "MANUFACTURER_DATA",
        ),
        (
            "Bidder must obtain the manufacturer's insurance certificate and test certificates.",
            "MANUFACTURER_INPUT",
            "Manufacturer certificates",
            "Obtain the manufacturer's insurance and test certificates.",
            "MANUFACTURER_DOCUMENT",
        ),
        (
            "The Bidder shall obtain Transformer technical datasheet values from the supplier.",
            "MANUFACTURER_INPUT",
            "Transformer technical datasheet values",
            "Technical values supplied for the transformer.",
            "MANUFACTURER_DATA",
        ),
        (
            "The Contractor shall Maintain Commercial General Liability insurance after award.",
            "POST_AWARD",
            "Maintain Commercial General Liability insurance",
            "Maintain insurance after award.",
            "ACTIVITY",
        ),
        (
            "The Bidder shall submit BID_RETURNABLE Schedule C - Insurance with its proposal.",
            "BID_RETURNABLE",
            "Schedule C - Insurance",
            "Complete and submit the insurance schedule.",
            "SUBMISSION_DOCUMENT",
        ),
    ],
)
def test_neighbour_cases_keep_the_model_classification(
    passage: str,
    lifecycle: str,
    title: str,
    description: str,
    item_kind: str,
) -> None:
    candidate = _classify(
        passage,
        _requirement(
            passage,
            lifecycle=lifecycle,
            title=title,
            description=description,
            item_kind=item_kind,
        ),
    )

    assert candidate.classification is not None
    assert candidate.classification.lifecycle.value == lifecycle
    assert candidate.classification.model_lifecycle.value == lifecycle


@pytest.mark.parametrize(
    "item",
    [
        _requirement(
            OPTION_PASSAGE,
            lifecycle="PRE_BID_ACTION",
            title="Proposal Withdrawal",
            item_kind="ACTIVITY",
            source_quote="The Bidder may withdraw a proposal using an unquoted process.",
        ),
        _requirement(
            INSURANCE_PASSAGE,
            lifecycle="MANUFACTURER_INPUT",
            title="Insurance Policy Details",
            item_kind="MANUFACTURER_DATA",
            source_quote="The Bidder must provide insurance details in an absent sentence.",
        ),
    ],
)
def test_unlocated_quote_reason_wins_over_new_guards(item: dict[str, Any]) -> None:
    passage = OPTION_PASSAGE if item["lifecycle"] == "PRE_BID_ACTION" else INSURANCE_PASSAGE
    candidate = _classify(passage, item)

    assert candidate.classification is not None
    assert candidate.classification.lifecycle is DiscoveryLifecycle.NEEDS_REVIEW
    assert candidate.classification.confidence.value == "LOW"
    assert (
        candidate.classification.review_reason
        == "The supporting sentence was not found in the source text"
    )


def test_prompt_additions_are_present_and_prompt_file_is_identical() -> None:
    manufacturer_sentence = (
        "Information about the bidder itself (its insurance, bonds, financial standing, "
        "references, company or signatory details) is not manufacturer input; it belongs in "
        "the form the bidder submits."
    )
    option_sentence = (
        'A right or option the passage gives the bidder (for example "may withdraw" or "may '
        'submit a new proposal") is not a requirement; list only what the passage obliges the '
        "bidder to do."
    )

    assert manufacturer_sentence in SYSTEM_PROMPT
    assert option_sentence in SYSTEM_PROMPT
    prompt_file = Path("docs/tasks/CIQ-STAGE2-prompt.txt").read_text(encoding="utf-8")
    assert prompt_file.rstrip("\n") == SYSTEM_PROMPT


@pytest.mark.parametrize(
    ("timing_text", "expected"),
    [
        ("invented deadline", None),
        ("three Business Days before closing", "three Business Days before closing"),
    ],
)
def test_guarded_timing_is_copied_only_when_it_occurs_in_the_passage(
    timing_text: str, expected: str | None
) -> None:
    passage = "The Bidder shall submit the form three Business Days before closing."
    candidate = _classify(passage, _requirement(passage, timing_text=timing_text))

    assert candidate.due_date_text == expected


def test_legacy_completion_uses_each_supplied_token_allowance() -> None:
    llm = _LegacyScript([{"requirements": []}, {"requirements": []}])
    first = _attempt_completion(
        llm,
        prompt="synthetic",
        system_prompt=SYSTEM_PROMPT,
        allowance=DISCOVERY_MAX_TOKENS,
        context_label="acceptance",
        timeout=None,
    )
    retry = _attempt_completion(
        llm,
        prompt="synthetic",
        system_prompt=SYSTEM_PROMPT,
        allowance=DISCOVERY_RETRY_MAX_TOKENS,
        context_label="acceptance",
        timeout=None,
    )

    assert first[1] is None and retry[1] is None
    assert llm.max_tokens == [DISCOVERY_MAX_TOKENS, DISCOVERY_RETRY_MAX_TOKENS]


@pytest.mark.parametrize(
    "reply",
    [
        '<think>maybe {x}</think>{"requirements": []}',
        '{"requirements": []}',
    ],
)
def test_structured_reply_strips_complete_think_blocks_before_json(reply: str) -> None:
    result = analyse_units(_StructuredScript([reply]), [("E-1", _unit("Synthetic passage text."))])

    assert result.candidates == ()
    assert result.outcomes[0].outcome.value == "ANALYSED"


def test_option_guard_explains_itself_even_when_the_model_gave_its_own_review_reason() -> None:
    candidate = _classify(
        OPTION_PASSAGE,
        _requirement(
            OPTION_PASSAGE,
            title="Proposal Withdrawal",
            lifecycle="PRE_BID_ACTION",
            item_kind="ACTIVITY",
            review_reason="Timing looks unusual",
        ),
    )

    assert candidate.classification is not None
    assert candidate.classification.review_reason == OPTION_REVIEW_REASON
    assert candidate.classification.lifecycle is DiscoveryLifecycle.NEEDS_REVIEW
