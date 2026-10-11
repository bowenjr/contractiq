"""Acceptance regressions: evidence integrity, legacy moves, cancellation race, retry markers."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from core.deliverable_discovery import (
    ExtractionStatus,
    ExtractionUnit,
    LocatorType,
    SourceCitation,
)
from core.deliverable_discovery_destinations import FindingFacts
from core.deliverable_discovery_jobs import DiscoveryJobRunner
from core.deliverable_discovery_review import build_review_view
from core.requirement_lifecycle import DestinationKind
from tests.unit.test_bid_package_governing_ui import BID_ID
from tests.unit.test_bid_package_governing_ui import governing_ui_app as _app_fixture
from tests.unit.test_bid_package_storage import BID_ID as JOBS_BID_ID
from tests.unit.test_deliverable_discovery_job_routes import _include, _start
from tests.unit.test_deliverable_discovery_jobs_recovery import PASSAGE, _jobs
from tests.unit.test_deliverable_discovery_run_outcomes import FakeClient
from tests.unit.test_deliverable_discovery_service import _pdf_bytes
from tests.unit.test_stage2_classification_repository import (
    NOW,
    _classification,
    _create_proposal,
    _repository,
)
from tests.unit.test_stage2_destinations import S_BID, _accept, _queue, _seed
from tests.unit.test_stage2_review import _get, _post


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    yield from _app_fixture.__wrapped__(tmp_path, monkeypatch)


# -- appended citations carry only verified evidence ---------------------------------------


def _second_source(repository: Any, text: str) -> None:
    from core.deliverable_discovery import DiscoveryRun, RunStatus
    from tests.unit.test_stage2_classification_repository import BID_ID as REPO_BID

    run = DiscoveryRun(
        bid_id=REPO_BID,
        operation_id="second-source",
        status=RunStatus.SUCCEEDED,
        proposal_count=0,
        actor="Reviewer",
        started_at=NOW,
        completed_at=NOW,
    )
    unit = ExtractionUnit(
        bid_id=REPO_BID,
        run_id=run.run_id,
        document_version_id="DV-SYNTHETIC-2",
        locator_type=LocatorType.PAGE,
        locator="p.8",
        text_excerpt=text,
        char_count=len(text),
        status=ExtractionStatus.OK,
        created_at=NOW,
    )
    repository.create_run(run, [unit], [], "Reviewer")


def _stored_quotes(repository: Any, proposal_id: str) -> list[tuple[str, str | None]]:
    stored = repository.get_proposal(proposal_id)
    return [(s["locator"], s.get("quote")) for s in stored["sources"]]


def test_appended_citation_keeps_a_located_quote_and_drops_an_invented_one(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    proposal = _create_proposal(repository, classification=_classification())
    passage = "Section 4. The Bidder shall also confirm the exact format. The end."
    _second_source(repository, passage)
    invented = SourceCitation(
        document_version_id="DV-SYNTHETIC-2",
        locator_type=LocatorType.PAGE,
        locator="p.8",
        excerpt="Totally invented surrounding text.",
        quote="The Bidder shall wire ten million dollars.",
    )
    repository.append_citations(proposal.proposal_id, [invented])
    stored = repository.get_proposal(proposal.proposal_id)
    appended = next(s for s in stored["sources"] if s["locator"] == "p.8")
    assert appended.get("quote") in (None, "")
    assert "invented" not in appended["excerpt"] and appended["excerpt"] == passage[:2000]


def test_appended_citation_quote_is_the_passage_text_not_the_callers_copy(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    proposal = _create_proposal(repository, classification=_classification())
    passage = "Section 4. The Bidder shall also confirm the exact format. The end."
    _second_source(repository, passage)
    loose = SourceCitation(
        document_version_id="DV-SYNTHETIC-2",
        locator_type=LocatorType.PAGE,
        locator="p.8",
        excerpt="ignored",
        quote="the bidder SHALL also confirm   the exact format.",
    )
    repository.append_citations(proposal.proposal_id, [loose])
    stored = repository.get_proposal(proposal.proposal_id)
    appended = next(s for s in stored["sources"] if s["locator"] == "p.8")
    assert appended["quote"] == "The Bidder shall also confirm the exact format."
    assert appended["quote"] in passage


# -- a legacy finding cannot be "moved" without effect --------------------------------------


def test_legacy_finding_cannot_be_reclassified_and_nothing_is_written(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    legacy = _create_proposal(repository, classification=None)
    [result] = repository.reclassify(
        legacy.bid_id, [legacy.proposal_id], "POST_AWARD", "Due after award", "Reviewer"
    )
    assert "recorded before lifecycle classification" in result["error"]
    stored = repository.get_proposal(legacy.proposal_id)
    assert stored["legacy"] is True and stored["reclassifications"] == []
    with repository._conn() as conn:  # type: ignore[attr-defined]
        audits = conn.execute(
            "SELECT count(*) FROM audit_log WHERE action=?",
            ("deliverable_discovery_proposal_reclassified",),
        ).fetchone()[0]
    assert audits == 0


# -- the review page ----------------------------------------------------------------------


def test_reclassify_route_accepts_tab_keys_and_turns_bad_input_into_a_notice(
    app: ModuleType,
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        rating = ids["Rating plate"]
        status, body = await _post(
            app,
            "/reclassify",
            [("proposal_id", rating), ("target_lifecycle", "post"), ("rationale", "After award")],
        )
        assert status == 200 and "1 of 1 finding(s) moved." in body
        for target in ("NOT_A_CATEGORY", "bid", "CONDITIONAL"):
            status, body = await _post(
                app,
                "/reclassify",
                [
                    ("proposal_id", ids["Form of Bid"]),
                    ("target_lifecycle", target),
                    ("rationale", "x"),
                    ("applies_at", "SOMETIME" if target == "CONDITIONAL" else ""),
                ],
            )
            if target == "bid":
                assert status == 200
            else:
                assert status == 422 and "not moved" in body and "Choose a category" in body

    asyncio.run(scenario())


class _LegacyModel:
    """A model that still answers in the pre-Stage-2 ``candidates`` shape."""

    def complete_json(self, *_args: object, **_kwargs: object) -> object:
        return {
            "candidates": [
                {
                    "title": "Legacy form",
                    "category": "Form",
                    "criticality": "REQUIRED",
                    "source_quote": S_BID,
                }
            ]
        }


def test_move_control_is_offered_for_classified_rows_only(app: ModuleType) -> None:
    async def scenario() -> None:
        await _seed(app, "Classified")
        assert "Move to another category" in await _get(app, "?lifecycle=bid")
        await _include(app, "Legacy", {"legacy.pdf": _pdf_bytes([S_BID])})
        app.discovery_job_runner.llm_client = _LegacyModel()
        await _start(app, "op-legacy")
        assert app.discovery_job_runner.run_pending() == 1
        legacy = [r for r in _queue(app) if r["title"] == "Legacy form"]
        assert legacy and legacy[0]["legacy"] is True
        page = await _get(app, "?lifecycle=review")
        assert "<strong>Legacy form</strong>" in page
        rows = {
            title: chunk
            for chunk in page.split("<tr")
            for title in ("Legacy form", "Rating plate")
            if f"<strong>{title}</strong>" in chunk
        }
        assert "Move to another category" not in rows["Legacy form"]
        assert "Move to another category" in rows["Rating plate"]
        status, body = await _post(
            app,
            "/reclassify",
            [
                ("proposal_id", legacy[0]["proposal_id"]),
                ("target_lifecycle", "POST_AWARD"),
                ("rationale", "x"),
            ],
        )
        assert status == 422 and "recorded before lifecycle classification" in body

    asyncio.run(scenario())


# -- cancellation that lands just before the run is finalized -------------------------------


def test_a_cancel_that_lands_before_finalize_ends_the_job_cancelled(tmp_path: Path) -> None:
    client = FakeClient("zero")
    _fx, service = _jobs(tmp_path, [PASSAGE], client)
    job = service.start_job(JOBS_BID_ID, "late-cancel", "Jason")
    real = service.jobs.finalize_run

    def cancel_then_finalize(*args: Any, **kwargs: Any) -> bool:
        service.cancel_job(job["job_id"])
        args = (*args[:-1], False)  # the runner's stale view: not cancelled
        return real(*args, **kwargs)

    service.jobs.finalize_run = cancel_then_finalize  # type: ignore[method-assign]
    result = DiscoveryJobRunner(service, client).process_job(job["job_id"])
    assert result and result["state"] == "CANCELLED"


# -- a retry finds a work item that was closed after a split-write failure ------------------


def test_origin_marker_is_found_even_when_the_work_item_was_closed(app: ModuleType) -> None:
    from core.work_items import WorkItemStatus

    async def scenario() -> None:
        ids = await _seed(app)
        writer = app.deliverable_discovery_service.destinations
        facts = FindingFacts(
            bid_id=BID_ID,
            proposal_id=ids["RFP receipt confirmation"],
            title="RFP receipt confirmation",
            description="Confirm receipt.",
            quote="Confirm receipt in writing.",
            source_label="rfp.pdf p.1",
            timing_text=None,
            due_date=None,
            due_note="no date stated",
            condition_text=None,
            required=True,
            commercial_topic=None,
        )
        first = writer.register(DestinationKind.WORK_ITEM, facts, "Jason")
        repository = app.work_item_service.repository
        item = repository.get(first.record_id)
        assert item is not None
        closed = [WorkItemStatus.CANCELLED]
        with repository._conn() as conn:  # type: ignore[attr-defined]
            conn.execute(
                "UPDATE work_items SET status=? WHERE work_item_id=?",
                (closed[0].value, first.record_id),
            )
        again = writer.register(DestinationKind.WORK_ITEM, facts, "Jason")
        assert again.record_id == first.record_id and again.reused is False
        assert len(repository.list(BID_ID)) == 1

    asyncio.run(scenario())


# -- review page navigation and destination display --------------------------------------


def test_the_still_needs_review_tile_opens_exactly_what_it_counts(app: ModuleType) -> None:
    async def scenario() -> None:
        await _seed(app)
        page = await _get(app, "")
        match = re.search(
            r'<a href="\?lifecycle=attention&amp;status=pending#proposals"[^>]*>'
            r"<strong>Still needs review</strong><span>(\d+) to review · (\d+) found",
            page,
        )
        assert match, "the tile must link to the combined view"
        opened = await _get(app, "?lifecycle=attention&status=pending")
        assert "<strong>Rating plate</strong>" in opened  # needs review
        assert "<strong>Spill containment plan</strong>" in opened  # conditional
        assert "<strong>Form of Bid</strong>" not in opened
        shown = opened.count('type="checkbox" name="proposal_id"')
        assert shown == int(match.group(1)) == 2

    asyncio.run(scenario())


def test_a_finding_cited_in_two_documents_is_listed_under_the_selected_one() -> None:
    row = {
        "proposal_id": "P1",
        "title": "Form of Bid",
        "current_decision": "PENDING",
        "lifecycle": "BID_RETURNABLE",
        "sources": [
            {"received_file_id": "A", "locator": "p.1"},
            {"received_file_id": "B", "locator": "p.7"},
        ],
    }
    names = {"A": "A - Instructions.pdf", "B": "B - Forms.pdf"}
    view = build_review_view([row], names, document="B")
    assert [heading for heading, _ in view.groups] == ["B - Forms.pdf"]
    assert [heading for heading, _ in build_review_view([row], names).groups] == [
        "A - Instructions.pdf"
    ]


def test_accepted_findings_link_to_the_register_that_holds_them(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        work = _accept(app, ids["RFP receipt confirmation"])
        deliverable = _accept(app, ids["Form of Bid"])
        manufacturer = _accept(app, ids["Transformer technical data"])
        pre = await _get(app, "?lifecycle=pre&status=accepted")
        assert f'href="/my-work/{work["record_id"]}"' in pre
        assert "Associate controlled version" not in pre  # nothing here is a deliverable
        bid = await _get(app, "?lifecycle=bid&status=accepted")
        assert f'href="/deliverables/{deliverable["deliverable_id"]}"' in bid
        assert "Associate controlled version" in bid  # deliverables keep their source action
        mfr = await _get(app, "?lifecycle=mfr&status=accepted")
        assert 'href="/vendor-documents/register"' in mfr and manufacturer["record_id"]
        assert "Associate controlled version" not in mfr

    asyncio.run(scenario())


def test_each_accept_button_says_what_it_will_create(app: ModuleType) -> None:
    async def scenario() -> None:
        await _seed(app)
        said = {r["title"]: r["accept_goes_to"] for r in _queue(app)}
        assert said["Form of Bid"] == "Creates a deliverable due with the bid"
        assert said["Inspection and Test Plan"] == "Creates a deliverable due after award"
        assert said["Transformer technical data"].startswith("Adds a requirement to the manufact")
        assert said["RFP receipt confirmation"] == "Creates an action in My Work"
        assert said["Insurance exceptions"].startswith("Adds this as evidence")
        assert said["Template clause"] == "" and said["Rating plate"] == ""  # cannot be accepted
        assert "Creates an action in My Work" in await _get(app, "?lifecycle=pre")

    asyncio.run(scenario())


def test_reason_required_messages_name_the_action_correctly(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        for action, word in (("rejected", "reject"), ("deferred", "defer")):
            status, body = await _post(
                app, "/decide", [("action", action), ("proposal_id", ids["Form of Bid"])]
            )
            assert status == 422 and f"A reason is required to {word} findings" in body

    asyncio.run(scenario())


def test_errors_and_notices_are_inside_the_review_section_the_forms_return_to(
    app: ModuleType,
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        status, body = await _post(
            app, "/decide", [("action", "rejected"), ("proposal_id", ids["Form of Bid"])]
        )
        section = body[body.index('id="proposals"') :]
        assert status == 422 and 'role="alert"' in section  # visible where the page lands
        page = await _get(app, "")
        assert page.count("deliverable-discovery/decide#proposals") >= 3
        assert "deliverable-discovery/reclassify#proposals" in page

    asyncio.run(scenario())
