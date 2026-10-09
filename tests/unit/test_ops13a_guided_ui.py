"""Guided workspace panel, supporting-page banner and the scope review controls."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from urllib.parse import urlencode

import pytest

from core.guided_context import bid_page_of
from scripts.asgi_acceptance_ops08 import request
from tests.unit.test_bid_package_governing_ui import (
    BID_ID,
    _register_release,
)
from tests.unit.test_bid_package_governing_ui import (
    governing_ui_app as _governing_ui_app,
)


@pytest.fixture
def guided_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    yield from _governing_ui_app.__wrapped__(tmp_path, monkeypatch)


def test_bid_page_of_reads_the_bid_from_path_or_query() -> None:
    assert bid_page_of("/bids/B-1/owner-decision", None) == ("B-1", "owner-decision")
    assert bid_page_of("/bids/B-1", None) == ("B-1", "")
    assert bid_page_of("/requirements", "B-2") == ("B-2", "requirements")
    assert bid_page_of("/my-day", None) is None


def test_workspace_shows_six_tasks_and_the_requested_task(guided_app: ModuleType) -> None:
    async def scenario() -> None:
        await _register_release(guided_app)
        status, _, page = await request(guided_app.app, "GET", f"/bids/{BID_ID}")
        assert status == 200
        html = page.decode()
        assert html.count('aria-label="Guided Bid workflow"') == 1
        assert html.count('class="task-chip') == 6
        assert "Choose and analyse files" in html
        assert 'id="stage-detail"' in html
        status, _, other = await request(guided_app.app, "GET", f"/bids/{BID_ID}?task=prepare")
        assert status == 200
        assert "<h2>Prepare the Bid</h2>" in other.decode()
        status, _, unknown = await request(guided_app.app, "GET", f"/bids/{BID_ID}?task=nonsense")
        assert status == 200 and "<h2>Choose and analyse files</h2>" in unknown.decode()

    asyncio.run(scenario())


def test_supporting_pages_carry_the_task_banner_and_the_workspace_does_not_double_it(
    guided_app: ModuleType,
) -> None:
    async def scenario() -> None:
        await _register_release(guided_app)
        for path in (
            f"/requirements?bid_id={BID_ID}",
            f"/documents?bid_id={BID_ID}",
            f"/bids/{BID_ID}/owner-decision",
            f"/bids/{BID_ID}/deliverable-discovery",
        ):
            status, _, page = await request(guided_app.app, "GET", path)
            assert status == 200, path
            html = page.decode()
            assert html.count("Where this page fits in the Bid workflow") == 1, path
            assert f"/bids/{BID_ID}?task=" in html, path
        status, _, workspace = await request(guided_app.app, "GET", f"/bids/{BID_ID}")
        assert workspace.decode().count("Where this page fits in the Bid workflow") == 0
        status, _, plain = await request(guided_app.app, "GET", "/my-day")
        assert status == 200
        assert b"Where this page fits" not in plain

    asyncio.run(scenario())


def test_scope_item_can_be_completed_and_accepted_from_its_page(guided_app: ModuleType) -> None:
    """A scope item is unresolved until Complete and Accepted; the page must offer both."""

    async def scenario() -> None:
        app = guided_app
        status, headers, _ = await request(
            app.app,
            "POST",
            "/scope-items",
            body=urlencode(
                {
                    "bid_id": BID_ID,
                    "title": "Crusher drive package",
                    "scope_area": "CORE_PRODUCTS",
                    "description": "Supply the package.",
                    "origin": "INTERNAL",
                    "customer_need": "REQUIRED",
                    "offer_position": "INCLUDED",
                    "pricing_state": "PRICED",
                    "materiality": "NON_MATERIAL",
                    "owner": "Jason",
                }
            ).encode(),
        )
        item = app.scope_repository.list_scope_items(BID_ID)[0]
        status, _, page = await request(app.app, "GET", f"/scope-items/{item.scope_item_id}")
        assert status == 200
        html = page.decode()
        for name in ("work_state", "review_state", "reviewer", "responsible_party"):
            assert f'name="{name}"' in html
        form = {
            "expected_version": str(item.version),
            "work_state": "COMPLETE",
            "review_state": "ACCEPTED",
            "reviewer": "Rita Reviewer",
            "responsible_party": "Bids and Contracts",
        }
        status, headers, _ = await request(
            app.app,
            "POST",
            f"/scope-items/{item.scope_item_id}",
            body=urlencode(form).encode(),
        )
        assert status == 303, headers
        saved = app.scope_repository.get_scope_item(item.scope_item_id)
        assert saved is not None
        assert saved.work_state.value == "COMPLETE" and saved.review_state.value == "ACCEPTED"
        assert re.search(r"Rita Reviewer", str(saved.reviewer))

    asyncio.run(scenario())


def test_commercial_position_saves_with_a_blank_customer_position(guided_app: ModuleType) -> None:
    """A blank customer position is an empty string; it must not fail as 'not a valid string'."""

    async def scenario() -> None:
        app = guided_app
        status, _, _ = await request(
            app.app,
            "POST",
            "/commercial",
            body=urlencode(
                {"bid_id": BID_ID, "operation": "initialize", "default_owner": "Jason"}
            ).encode(),
        )
        assert status in (200, 303)
        position = app.ops08_repository.workspace(BID_ID)[0]
        position_id = str(position["position_id"])
        detail = app.ops08_repository.detail(position_id)
        form = {
            "expected_version": str(detail["current_version"]),
            "customer_position": "",
            "proposed_position": "Our standard wording applies.",
            "disposition": "NOT_APPLICABLE",
            "rationale": "Not addressed in the customer package.",
            "owner": "Jason",
            "negotiation_state": "NOT_REQUIRED",
        }
        status, headers, body = await request(
            app.app,
            "POST",
            f"/commercial/positions/{position_id}",
            body=urlencode(form).encode(),
        )
        assert status == 303, body[:600]
        assert b"valid string" not in body

    asyncio.run(scenario())
