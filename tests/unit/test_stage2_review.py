"""Stage 2 — the review screen: bid returnables first, other categories one click away."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import ModuleType
from urllib.parse import urlencode

import pytest

from core.deliverable_discovery_review import build_review_view
from scripts.asgi_acceptance_ops08 import request
from tests.unit.test_bid_package_governing_ui import BID_ID
from tests.unit.test_bid_package_governing_ui import governing_ui_app as _app_fixture
from tests.unit.test_stage2_destinations import _queue, _seed

PAGE = f"/bids/{BID_ID}/deliverable-discovery"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    yield from _app_fixture.__wrapped__(tmp_path, monkeypatch)


def _text(body: bytes) -> str:
    return re.sub(r"\s+", " ", body.decode())


async def _get(app: ModuleType, query: str = "") -> str:
    status, _, body = await request(app.app, "GET", PAGE + query)
    assert status == 200
    return _text(body)


async def _post(app: ModuleType, path: str, pairs: list[tuple[str, str]]) -> tuple[int, str]:
    status, _, body = await request(
        app.app,
        "POST",
        PAGE + path,
        body=urlencode(pairs).encode(),
        content_type="application/x-www-form-urlencoded",
    )
    return status, _text(body)


# -- the pure view ---------------------------------------------------------------------


def _row(index: int, lifecycle: str | None, decision: str = "PENDING", file_id: str = "F1") -> dict:
    return {
        "proposal_id": f"P{index}",
        "title": f"Item {index}",
        "current_decision": decision,
        "lifecycle": lifecycle,
        "sources": [{"received_file_id": file_id, "locator": f"p.{index}"}],
    }


QUEUE = [
    _row(1, "BID_RETURNABLE"),
    _row(2, "BID_RETURNABLE", "ACCEPTED"),
    _row(3, "POST_AWARD", file_id="F2"),
    _row(4, "MANUFACTURER_INPUT"),
    _row(5, "NOT_APPLICABLE"),
    _row(6, None),  # a finding recorded before classification
    _row(7, "CONDITIONAL"),
]
NAMES = {"F1": "Part 1/Instructions.pdf", "F2": "Part 3/VDDR.pdf"}


def test_each_category_shows_only_its_findings_and_counts_stay_visible() -> None:
    view = build_review_view(QUEUE, NAMES, lifecycle="bid")
    assert [r["proposal_id"] for r in view.rows] == ["P1"]  # pending only
    assert view.counts["accepted"] == 1 and view.counts["all"] == 2
    assert view.lifecycle_counts["post"] == {"pending": 1, "total": 1}
    assert view.lifecycle_counts["all"] == {"pending": 6, "total": 7}
    assert [r["proposal_id"] for r in build_review_view(QUEUE, NAMES, lifecycle="post").rows] == [
        "P3"
    ]
    assert [r["proposal_id"] for r in build_review_view(QUEUE, NAMES, lifecycle="na").rows] == [
        "P5"
    ]


def test_unclassified_findings_are_under_needs_review() -> None:
    view = build_review_view(QUEUE, NAMES, lifecycle="review")
    assert [r["proposal_id"] for r in view.rows] == ["P6"]


def test_unknown_values_fall_back_and_all_groups_by_category_then_document() -> None:
    assert build_review_view(QUEUE, NAMES, lifecycle="zzz", group="zzz").lifecycle == "all"
    view = build_review_view(QUEUE, NAMES, lifecycle="all", group="lifecycle")
    headings = [heading for heading, _ in view.groups]
    assert headings[0].startswith("With the bid — ")
    assert any(h.startswith("After award — VDDR.pdf") for h in headings)
    by_document = build_review_view(QUEUE, NAMES, lifecycle="all", group="document")
    assert [h for h, _ in by_document.groups][:1] == ["Instructions.pdf"]


def test_filters_combine_with_status_document_and_paging() -> None:
    many = [_row(i, "BID_RETURNABLE") for i in range(1, 251)]
    view = build_review_view(many, NAMES, lifecycle="bid", page=3, page_size=100)
    assert view.page == 3 and len(view.rows) == 50 and view.matching == 250
    # a document that has no finding in this category falls back to all documents (Stage 1 rule)
    assert build_review_view(many, NAMES, lifecycle="bid", document="F2").document == ""


# -- the page --------------------------------------------------------------------------


def test_default_view_is_bid_returnables_and_other_findings_are_a_click_away(
    app: ModuleType,
) -> None:
    async def scenario() -> None:
        await _seed(app)
        page = await _get(app)
        assert (
            "<strong>Form of Bid</strong>" in page
            and "<strong>Insurance exceptions</strong>" in page
        )
        assert "<strong>Inspection and Test Plan</strong>" not in page  # post-award is its own view
        assert "<strong>Transformer technical data</strong>" not in page
        assert "Include with the bid" in page and "Owed after award" in page
        assert "Obtain from the manufacturer" in page and "Still needs review" in page
        assert "Clarify or arrange before bidding" in page
        post = await _get(app, "?lifecycle=post")
        assert (
            "<strong>Inspection and Test Plan</strong>" in post
            and "<strong>Form of Bid</strong>" not in post
        )
        assert "Review: After award" not in post
        mfr = await _get(app, "?lifecycle=mfr")
        assert "<strong>Transformer technical data</strong>" in mfr
        na = await _get(app, "?lifecycle=na")
        assert "<strong>Template clause</strong>" in na
        review = await _get(app, "?lifecycle=review")
        assert (
            "<strong>Rating plate</strong>" in review
            and "The analysis suggested bid returnable" in review
        )

    asyncio.run(scenario())


def test_citations_and_classification_facts_are_visible_on_each_row(app: ModuleType) -> None:
    async def scenario() -> None:
        await _seed(app)
        page = await _get(app, "?lifecycle=bid")
        assert (
            "<mark>The Bidder shall submit a signed Form of Bid with the proposal.</mark>" in page
        )
        assert "rfp.pdf" in page and "Page p.1" in page
        assert "confidence high" in page

    asyncio.run(scenario())


def test_accepting_in_one_category_keeps_progress_in_the_others(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        status, body = await _post(
            app,
            "/decide",
            [
                ("bulk_action", "accept"),
                ("proposal_id", ids["Form of Bid"]),
                ("proposal_id", ids["Insurance exceptions"]),
                ("status", "pending"),
                ("lifecycle", "bid"),
            ],
        )
        assert status == 200
        assert "2 of 2 finding(s) accepted." in body
        assert "1 Deliverable created" in body and "1 commercial position update created" in body
        # the page stays on the same category and the other categories are untouched
        assert "Include with the bid" in body
        post = await _get(app, "?lifecycle=post")
        assert "<strong>Inspection and Test Plan</strong>" in post
        accepted = await _get(app, "?lifecycle=bid&status=accepted")
        assert "<strong>Form of Bid</strong>" in accepted and "Created: deliverable" in accepted

    asyncio.run(scenario())


def test_reclassify_one_and_in_bulk_with_a_reason(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        status, body = await _post(
            app,
            "/reclassify",
            [
                ("proposal_id", ids["Rating plate"]),
                ("target_lifecycle", "POST_AWARD"),
                ("rationale", "Due after award"),
                ("lifecycle", "review"),
            ],
        )
        assert status == 200 and "1 of 1 finding(s) moved." in body
        assert "<strong>Rating plate</strong>" not in await _get(app, "?lifecycle=review")
        assert "<strong>Rating plate</strong>" in await _get(app, "?lifecycle=post")
        # no reason, nothing selected and a missing target are refused with guidance
        for pairs, expected in (
            ([("proposal_id", ids["Form of Bid"]), ("target_lifecycle", "POST_AWARD")], "reason"),
            ([("target_lifecycle", "POST_AWARD"), ("rationale", "x")], "No findings are selected"),
            ([("proposal_id", ids["Form of Bid"]), ("rationale", "x")], "Choose where to move"),
        ):
            status, body = await _post(app, "/reclassify", pairs)
            assert status == 422 and expected in body
        assert {r["lifecycle"] for r in _queue(app) if r["title"] == "Form of Bid"} == {
            "BID_RETURNABLE"
        }

    asyncio.run(scenario())


def test_a_blocked_finding_cannot_be_accepted_from_the_page(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        page = await _get(app, "?lifecycle=na")
        assert "Confirmed not applicable items are rejected, not accepted" in page
        status, body = await _post(
            app,
            "/decide",
            [("action", "accepted"), ("proposal_id", ids["Template clause"]), ("lifecycle", "na")],
        )
        assert "could not be accepted" in body
        assert {r["current_decision"] for r in _queue(app) if r["title"] == "Template clause"} == {
            "PENDING"
        }

    asyncio.run(scenario())


def test_the_review_partial_never_nests_a_form_inside_a_form() -> None:
    from html.parser import HTMLParser

    class Nesting(HTMLParser):
        depth = 0
        nested = False

        def handle_starttag(self, tag: str, attrs: list) -> None:
            if tag == "form":
                self.nested |= self.depth > 0
                self.depth += 1

        def handle_endtag(self, tag: str) -> None:
            if tag == "form":
                self.depth -= 1

    source = Path("templates/_discovery_review.html").read_text()
    source = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", "", source, flags=re.S)
    parser = Nesting()
    parser.feed(source)
    assert not parser.nested
