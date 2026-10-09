"""Stage 1 / Fixes 1 and 6: bulk review acts on exactly the selected findings; the queue is
filterable and pageable; no template nests one form inside another."""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType
from urllib.parse import urlencode

import pytest

from core.deliverable_discovery_review import build_review_view
from scripts.asgi_acceptance_ops08 import request
from tests.unit.test_bid_package_governing_ui import BID_ID
from tests.unit.test_bid_package_governing_ui import governing_ui_app as _app_fixture
from tests.unit.test_deliverable_discovery_job_routes import _include, _start
from tests.unit.test_deliverable_discovery_service import _pdf_bytes


@pytest.fixture
def job_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    yield from _app_fixture.__wrapped__(tmp_path, monkeypatch)


PASSAGE = "Submit the completed signed forms listed below to Procurement with the offer now."
DECIDE = f"/bids/{BID_ID}/deliverable-discovery/decide"
PAGE = f"/bids/{BID_ID}/deliverable-discovery"
TITLES = [f"Form {index:02d}" for index in range(1, 13)]


class ManyFindingsClient:
    """Twelve distinct findings from one passage; the last one has unclear criticality."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        return {
            "candidates": [
                {
                    "title": title,
                    "category": "FORM",
                    "criticality": "UNCLEAR" if title == TITLES[-1] else "REQUIRED",
                    "condition_text": None,
                    "due_date": None,
                    "requested_format": "PDF",
                    "requested_recipient": "Procurement",
                    "proposed_owner": None,
                    "due_date_text": None,
                    "source_quote": None,
                }
                for title in TITLES
            ]
        }


def _post(app: ModuleType, pairs: list[tuple[str, str]]):
    return request(
        app.app,
        "POST",
        DECIDE,
        body=urlencode(pairs).encode(),
        content_type="application/x-www-form-urlencoded",
    )


async def _seed(app: ModuleType, folder: str = "Many") -> dict[str, str]:
    """Run a real discovery job and return ``{title: proposal_id}``."""
    await _include(app, folder, {"forms.pdf": _pdf_bytes([PASSAGE])})
    app.discovery_job_runner.llm_client = ManyFindingsClient()
    await _start(app, f"op-{folder}")
    assert app.discovery_job_runner.run_pending() == 1
    queue = app.deliverable_discovery_service.list_review_queue(BID_ID)
    assert len(queue) == len(TITLES)
    return {row["title"]: row["proposal_id"] for row in queue}


def _decisions(app: ModuleType) -> dict[str, str]:
    queue = app.deliverable_discovery_service.list_review_queue(BID_ID)
    return {row["title"]: row["current_decision"] for row in queue}


def _count_deliverables(app: ModuleType) -> int:
    return len(app.deliverable_repository.list(BID_ID))


def _event_rows(app: ModuleType) -> int:
    with app.db._conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM deliverable_discovery_review_events").fetchone()[
            0
        ]


def _text(body: bytes) -> str:
    return re.sub(r"\s+", " ", body.decode())


# ------------------------------------------------------------------------ bulk actions


def test_bulk_accept_acts_on_exactly_the_three_selected(job_app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(job_app)
        chosen = [TITLES[1], TITLES[4], TITLES[7]]
        pairs = [("bulk_action", "accept")] + [("proposal_id", ids[t]) for t in chosen]
        status, _, body = await _post(job_app, pairs)

        assert status == 200
        assert "3 of 3 finding(s) accepted" in _text(body)
        state = _decisions(job_app)
        assert sorted(t for t, d in state.items() if d == "ACCEPTED") == sorted(chosen)
        assert sum(1 for d in state.values() if d == "PENDING") == len(TITLES) - 3
        assert _count_deliverables(job_app) == 3

    asyncio.run(scenario())


def test_bulk_action_with_nothing_selected_changes_nothing(job_app: ModuleType) -> None:
    async def scenario() -> None:
        await _seed(job_app)
        before = (_event_rows(job_app), _count_deliverables(job_app))
        status, _, body = await _post(job_app, [("bulk_action", "accept")])

        assert status == 422
        assert "No findings are selected" in _text(body)
        assert (_event_rows(job_app), _count_deliverables(job_app)) == before
        assert set(_decisions(job_app).values()) == {"PENDING"}

    asyncio.run(scenario())


def test_a_contradictory_request_is_refused_whole(job_app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(job_app)
        before = _event_rows(job_app)
        pairs = [
            ("action", "accepted"),
            ("bulk_action", "accept"),
            ("proposal_id", ids[TITLES[0]]),
            ("proposal_id", ids[TITLES[1]]),
        ]
        status, _, body = await _post(job_app, pairs)
        assert status == 422 and "contradictory" in _text(body)
        assert _event_rows(job_app) == before

    asyncio.run(scenario())


def test_a_single_row_action_never_takes_extra_findings_with_it(job_app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(job_app)
        pairs = [
            ("action", "accepted"),
            ("proposal_id", ids[TITLES[0]]),
            ("proposal_id", ids[TITLES[1]]),
        ]
        status, _, _ = await _post(job_app, pairs)
        assert status == 422
        assert set(_decisions(job_app).values()) == {"PENDING"}

        status, _, _ = await _post(
            job_app, [("action", "accepted"), ("proposal_id", ids[TITLES[0]])]
        )
        assert status == 200
        state = _decisions(job_app)
        assert [t for t, d in state.items() if d == "ACCEPTED"] == [TITLES[0]]

    asyncio.run(scenario())


def test_the_same_finding_selected_twice_is_refused(job_app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(job_app)
        pid = ids[TITLES[0]]
        status, _, body = await _post(
            job_app, [("bulk_action", "accept"), ("proposal_id", pid), ("proposal_id", pid)]
        )
        assert status == 422 and "twice" in _text(body)
        assert set(_decisions(job_app).values()) == {"PENDING"}

    asyncio.run(scenario())


def test_bulk_reject_needs_a_reason_and_keeps_the_evidence(job_app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(job_app)
        chosen = TITLES[2:6]
        pairs = [("bulk_action", "reject")] + [("proposal_id", ids[t]) for t in chosen]

        status, _, body = await _post(job_app, pairs)
        assert status == 422 and "reason is required" in _text(body)
        assert set(_decisions(job_app).values()) == {"PENDING"}

        proposals_before = len(app_proposals(job_app))
        events_before = _event_rows(job_app)
        status, _, body = await _post(job_app, [*pairs, ("rationale", "Not a returnable")])
        assert status == 200 and "4 of 4 finding(s) rejected" in _text(body)

        state = _decisions(job_app)
        assert sorted(t for t, d in state.items() if d == "REJECTED") == sorted(chosen)
        assert _count_deliverables(job_app) == 0
        # Evidence is kept: the findings are still there, and each decision is a new event row.
        assert len(app_proposals(job_app)) == proposals_before
        assert _event_rows(job_app) == events_before + 4

    asyncio.run(scenario())


def app_proposals(app: ModuleType) -> list[dict]:
    return app.deliverable_discovery_service.list_review_queue(BID_ID)


def test_unclear_criticality_findings_can_be_rejected_in_bulk(job_app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(job_app)
        status, _, _ = await _post(
            job_app,
            [
                ("bulk_action", "reject"),
                ("proposal_id", ids[TITLES[-1]]),
                ("rationale", "Not applicable"),
            ],
        )
        assert status == 200
        assert _decisions(job_app)[TITLES[-1]] == "REJECTED"

    asyncio.run(scenario())


def test_failures_in_a_bulk_accept_are_counted_and_named(job_app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(job_app)
        first = ids[TITLES[0]]
        await _post(job_app, [("action", "accepted"), ("proposal_id", first)])
        pairs = [
            ("bulk_action", "accept"),
            ("proposal_id", first),  # already accepted
            ("proposal_id", ids[TITLES[1]]),
            ("proposal_id", ids[TITLES[2]]),
        ]
        status, _, body = await _post(job_app, pairs)
        text = _text(body)
        assert status == 422
        assert "2 of 3 finding(s) accepted" in text
        assert "1 could not be accepted" in text and "already accepted" in text
        assert _count_deliverables(job_app) == 3

    asyncio.run(scenario())


# ------------------------------------------------------------------- filtering and paging


def test_default_view_shows_only_pending_and_the_status_links_count_each_state(
    job_app: ModuleType,
) -> None:
    async def scenario() -> None:
        ids = await _seed(job_app)
        await _post(job_app, [("action", "accepted"), ("proposal_id", ids[TITLES[0]])])
        await _post(
            job_app,
            [
                ("bulk_action", "reject"),
                ("proposal_id", ids[TITLES[1]]),
                ("proposal_id", ids[TITLES[2]]),
                ("rationale", "No"),
            ],
        )
        _, _, body = await request(job_app.app, "GET", PAGE)
        text = _text(body)
        assert "To review (9)" in text and "Accepted (1)" in text and "Rejected (2)" in text
        assert text.count('name="proposal_id" value=') >= 9

        _, _, rejected = await request(job_app.app, "GET", f"{PAGE}?status=rejected")
        shown = re.findall(r"<strong>(Form \d\d)</strong>", rejected.decode())
        assert shown == [TITLES[1], TITLES[2]]

        _, _, junk = await request(job_app.app, "GET", f"{PAGE}?status=nonsense&page=abc")
        assert b"To review (9)" in junk

    asyncio.run(scenario())


def test_each_finding_shows_its_source_document_and_supporting_text(job_app: ModuleType) -> None:
    async def scenario() -> None:
        await _seed(job_app)
        _, _, body = await request(job_app.app, "GET", PAGE)
        text = _text(body)
        assert "forms.pdf" in text  # the document, not an internal id
        assert "Page p.1" in text
        assert "Submit the completed signed forms" in text  # the supporting text

    asyncio.run(scenario())


# ------------------------------------------------------------ build_review_view (pure)


def _row(index: int, decision: str, file_id: str, locator: str) -> dict:
    return {
        "proposal_id": f"P{index}",
        "title": f"T{index}",
        "current_decision": decision,
        "sources": [{"received_file_id": file_id, "locator": locator}],
    }


def test_view_orders_by_document_then_page_naturally() -> None:
    queue = [
        _row(1, "PENDING", "f2", "p.10"),
        _row(2, "PENDING", "f1", "p.9"),
        _row(3, "PENDING", "f1", "p.2"),
        _row(4, "PENDING", "f2", "p.2"),
    ]
    names = {"f1": "a/Instructions.pdf", "f2": "a/Terms.pdf"}
    view = build_review_view(queue, names)
    assert [r["proposal_id"] for r in view.rows] == ["P3", "P2", "P4", "P1"]


def test_view_filters_by_status_and_document_and_counts_everything() -> None:
    queue = [
        _row(1, "PENDING", "f1", "p.1"),
        _row(2, "ACCEPTED", "f1", "p.2"),
        _row(3, "PENDING", "f2", "p.1"),
        _row(4, "REJECTED", "f2", "p.2"),
    ]
    names = {"f1": "One.pdf", "f2": "Two.pdf"}
    view = build_review_view(queue, names, status="pending", document="f2")
    assert [r["proposal_id"] for r in view.rows] == ["P3"]
    assert dict(view.counts) == {"pending": 2, "accepted": 1, "rejected": 1, "all": 4}
    assert [d["name"] for d in view.documents] == ["One.pdf", "Two.pdf"]
    assert build_review_view(queue, names, status="all").matching == 4


def test_view_pages_hundreds_of_findings_and_clamps_bad_pages() -> None:
    queue = [_row(i, "PENDING", "f1", f"p.{i}") for i in range(1, 451)]
    view = build_review_view(queue, {"f1": "Big.pdf"}, page=2)
    assert (view.page, view.page_count, len(view.rows), view.matching) == (2, 5, 100, 450)
    assert view.rows[0]["proposal_id"] == "P101"
    assert build_review_view(queue, {}, page=99).page == 5
    assert build_review_view(queue, {}, page=-3).page == 1
    assert build_review_view(queue, {}, status="bogus").status == "pending"
    assert build_review_view(queue, {}, document="unknown").document == ""


# --------------------------------------------------------- no nested forms (structural)


class _FormNesting(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.nested = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "form":
            self.depth += 1
            self.nested = self.nested or self.depth > 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self.depth:
            self.depth -= 1


@pytest.mark.parametrize(
    "template", sorted(p.name for p in (Path(__file__).parents[2] / "templates").glob("*.html"))
)
def test_no_template_nests_a_form_inside_a_form(template: str) -> None:
    """A browser folds the hidden fields of an inner form into the outer one, so a row's Accept
    can carry other rows' ids. Association by ``form=`` attribute is the supported alternative."""
    source = (Path(__file__).parents[2] / "templates" / template).read_text()
    # Jinja control tags do not change the nesting of the tags around them.
    source = re.sub(r"\{%.*?%\}|\{#.*?#\}", "", source, flags=re.S)
    parser = _FormNesting()
    parser.feed(source)
    assert not parser.nested, f"{template} nests a <form> inside another <form>"
