"""Stage 2.1 regressions for standing-conduct and refined option guards."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.deliverable_discovery_llm import SYSTEM_PROMPT
from core.requirement_lifecycle import DiscoveryLifecycle, guard_classification

FIXTURE_PATH = Path("tests/fixtures/stage2_1_itb_prebid_quotes.json")
STANDING_CONDUCT_REASON = (
    "Standing conduct rule that governs the bidder; not an activity to complete before bidding. "
    "Suggested: not a pre-bid action — reject, or keep as reference."
)
OPTION_REASON = (
    'The passage gives the bidder an option ("may"), not an obligation. Reject it, or move '
    "it if it is a real requirement."
)
OWNER_RIGHT_REASON = (
    "The passage grants a right to the Owner, not an option for the bidder. Suggested: "
    "right held by the Owner — reject, or record separately."
)


def _raw(source_quote: str, **overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "lifecycle": "PRE_BID_ACTION",
        "requirement_status": "REQUIRED",
        "item_kind": "ACTIVITY",
        "description": "Synthetic classification candidate.",
        "confidence": "HIGH",
        "source_quote": source_quote,
    }
    raw.update(overrides)
    return raw


def _itb_cases() -> list[dict[str, str]]:
    loaded = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


@pytest.mark.parametrize("case", _itb_cases(), ids=lambda case: case["title"])
def test_itb_pre_bid_benchmark_routes_only_real_pre_bid_activities(
    case: dict[str, str],
) -> None:
    classification = guard_classification(
        _raw(case["source_quote"]),
        case["passage_text"],
    )

    assert classification.lifecycle.value == case["expected_lifecycle"]
    assert classification.model_lifecycle is DiscoveryLifecycle.PRE_BID_ACTION
    if classification.lifecycle is DiscoveryLifecycle.NEEDS_REVIEW:
        assert classification.review_reason == STANDING_CONDUCT_REASON


@pytest.mark.parametrize(
    "quote",
    [
        "The Bidder must submit notice of intent to bid.",
        "The Bidder may be required to attend a pre-bid meeting.",
        (
            "The Bidder must, prior to submitting any Proposal, obtain the approvals and "
            "comply with all applicable laws."
        ),
    ],
)
def test_g9_neighbours_remain_pre_bid_actions(quote: str) -> None:
    classification = guard_classification(_raw(quote), quote)

    assert classification.lifecycle is DiscoveryLifecycle.PRE_BID_ACTION


@pytest.mark.parametrize(
    ("title", "quote", "model_lifecycle", "expected_lifecycle", "expected_reason"),
    [
        (
            "Alternative Proposal",
            "Bidders may, in addition to submitting a fully conforming Proposal, submit "
            "alternative Proposal(s) offering different materials, equipment or terms provided "
            "such Alternative Bid(s) are clearly identified as “Alternative Bid”",
            "BID_RETURNABLE",
            "NEEDS_REVIEW",
            OPTION_REASON,
        ),
        (
            "Witness Performance Test",
            "The Supplier may provide a representative at the Site to witness the Performance "
            "Test.",
            "POST_AWARD",
            "NEEDS_REVIEW",
            OPTION_REASON,
        ),
        (
            "Status reporting against individual production schedules",
            "Depending on the extent and complexity of the Work being carried out by "
            "Subsuppliers, it may be necessary to report status against the individual "
            "production schedules.",
            "POST_AWARD",
            "NEEDS_REVIEW",
            OPTION_REASON,
        ),
        (
            "Inspection Release Certificate",
            "No equipment may be shipped by a Vendor, unless it is in possession of an "
            "Inspection Release certificate issued for the Equipment to be shipped.",
            "POST_AWARD",
            "POST_AWARD",
            None,
        ),
        (
            "Evidence of no liens or claims",
            "Owner may withhold payment of invoices until Supplier furnishes such evidence.",
            "POST_AWARD",
            "POST_AWARD",
            None,
        ),
        (
            "Compliance audit access",
            "The Owner may audit compliance at any time.",
            "POST_AWARD",
            "NEEDS_REVIEW",
            OWNER_RIGHT_REASON,
        ),
    ],
)
def test_all_acceptance_g7_firings_have_refined_outcomes(
    title: str,
    quote: str,
    model_lifecycle: str,
    expected_lifecycle: str,
    expected_reason: str | None,
) -> None:
    classification = guard_classification(
        _raw(
            quote,
            lifecycle=model_lifecycle,
            description=title,
        ),
        quote,
    )

    assert classification.lifecycle.value == expected_lifecycle
    assert classification.model_lifecycle.value == model_lifecycle
    assert classification.review_reason == expected_reason


def test_g7_reason_wins_when_an_option_is_also_a_standing_rule() -> None:
    quote = "Bidders may adhere to the Owner's communication procedures."

    classification = guard_classification(_raw(quote), quote)

    assert classification.lifecycle is DiscoveryLifecycle.NEEDS_REVIEW
    assert classification.review_reason == OPTION_REASON


@pytest.mark.parametrize(
    "quote",
    [
        "The Supplier may not ship equipment without an Inspection Release certificate.",
        "The Supplier shall not ship equipment that may expose the load to damage.",
        "Payment may be withheld until Supplier furnishes evidence of no liens.",
    ],
)
def test_prohibitive_or_conditioned_may_is_not_a_bidder_option(quote: str) -> None:
    classification = guard_classification(_raw(quote, lifecycle="POST_AWARD"), quote)

    assert classification.lifecycle is DiscoveryLifecycle.POST_AWARD
    assert classification.review_reason is None


def test_condition_does_not_hide_a_supplier_subject_option() -> None:
    quote = "The Supplier may submit an alternative subject to identifying all deviations."

    classification = guard_classification(_raw(quote, lifecycle="POST_AWARD"), quote)

    assert classification.lifecycle is DiscoveryLifecycle.NEEDS_REVIEW
    assert classification.review_reason == OPTION_REASON


def test_another_named_partys_unconditioned_right_has_a_distinct_reason() -> None:
    quote = "The Purchaser may audit compliance at any time."

    classification = guard_classification(_raw(quote, lifecycle="POST_AWARD"), quote)

    assert classification.lifecycle is DiscoveryLifecycle.NEEDS_REVIEW
    assert classification.review_reason == OWNER_RIGHT_REASON.replace("Owner", "Purchaser")


def test_existing_unlocated_quote_reason_wins_over_g9() -> None:
    quote = "Bidders must not disclose the contents of their Proposal."

    classification = guard_classification(_raw(quote), "An unrelated source passage.")

    assert classification.lifecycle is DiscoveryLifecycle.NEEDS_REVIEW
    assert (
        classification.review_reason == "The supporting sentence was not found in the source text"
    )


def test_discovery_prompt_keeps_standing_rules_for_review_instead_of_dropping_them() -> None:
    """A conduct rule is retained as NEEDS_REVIEW; the first wording made the model omit it."""
    assert "is not a pre-bid action" in SYSTEM_PROMPT
    assert "Do not drop a standing rule" in SYSTEM_PROMPT
    assert "lifecycle NEEDS_REVIEW" in SYSTEM_PROMPT
    assert STANDING_CONDUCT_REASON in SYSTEM_PROMPT


def test_signing_a_confidentiality_agreement_stays_a_pre_bid_action() -> None:
    """Executing a confidentiality document is an activity; only the standing rule is not."""
    quote = "The Recipient must not disclose Confidential Information until it has signed."
    classification = guard_classification(
        _raw(
            quote,
            title="Confidentiality and Non-Disclosure Agreement",
            description="The Recipient must enter into and sign the NDA to receive information.",
        ),
        quote,
    )

    assert classification.lifecycle is DiscoveryLifecycle.PRE_BID_ACTION
    assert classification.review_reason is None
