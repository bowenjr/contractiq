"""Package Intake wording, intake-vs-whole-Bid separation and read-only navigation."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from urllib.parse import urlencode

import pytest

from core.bid_workflow import intake_heading
from scripts.asgi_acceptance_ops08 import request
from tests.unit.test_bid_package_governing_ui import (
    BID_ID,
    _register_release,
)
from tests.unit.test_bid_package_governing_ui import (
    governing_ui_app as _governing_ui_app,
)

ROOT = f"/bids/{BID_ID}/package-intake-addenda"
WATCHED = ("commercial_positions", "commercial_items", "audit_log", "bid_basis_snapshots")


@pytest.fixture
def intake_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    yield from _governing_ui_app.__wrapped__(tmp_path, monkeypatch)


def _counts(app: ModuleType) -> dict[str, int]:
    with sqlite3.connect(app.db.db_path) as conn:
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in WATCHED}


async def _page(app: ModuleType, path: str) -> str:
    status, _, body = await request(app.app, "GET", path)
    assert status == 200, body[:300]
    return body.decode()


def test_not_in_basis_heading_is_named_for_the_release_type() -> None:
    code = "RECEIVED_RELEASE_NOT_IN_BASIS"
    assert intake_heading(code, "x", "INITIAL_PACKAGE") == "Initial package not yet in Bid Basis"
    assert intake_heading(code, "x", "ADDENDUM") == "Addendum not incorporated"
    assert intake_heading(code, "x") == "Addendum not incorporated"


def test_initial_and_addendum_receipts_get_stage_specific_wording_and_groups(
    intake_app: ModuleType,
) -> None:
    import asyncio

    async def scenario() -> None:
        await _register_release(intake_app, reference="")
        await _register_release(
            intake_app,
            folder="Addendum-01",
            release_type="ADDENDUM",
            reference="Addendum 01",
            mode="addendum",
        )
        page = await _page(intake_app, ROOT)
        assert "Initial package not yet in Bid Basis" in page
        assert "Addendum not incorporated" in page
        assert "Initial package (receipt 1) has no confirmed customer reference" in page
        assert "Addendum Addendum 01 is received but not yet confirmed" in page
        # Separation: both labelled status parts and the three groups are present.
        assert "Package intake:" in page and "Development gate check:" in page
        assert "Needed for this package" in page
        assert "Bid Basis (after document control)" in page
        assert "Partly reviewed" in page or "Review and incorporation outstanding" in page
        # The next required action is a package-intake step, not a later-stage one.
        match = re.search(r'id="next-action".*?<h2>(.*?)</h2>', page, re.S)
        assert match is not None and "commercial" not in match.group(1).lower()
        # Later stages show a hint instead of an action link.
        assert "Later stage" in page
        assert "Resolve a commercial or contract position" not in page

    asyncio.run(scenario())


def test_whole_bid_count_matches_global_gate_and_scopes_partition(
    intake_app: ModuleType,
) -> None:
    import asyncio

    async def scenario() -> None:
        await _register_release(intake_app)
        page = await _page(intake_app, ROOT)
        total = int(re.search(r"What is outstanding \((\d+) blocking\)", page).group(1))  # type: ignore[union-attr]
        groups = [
            int(n) for n in re.findall(r'class="group-heading">[^<]*\((\d+) blocking\)', page)
        ]
        whole = re.search(r"Development gate check: [^—]*— (\d+) later-stage", page)
        assert whole is not None
        assert sum(groups) == total
        assert int(whole.group(1)) <= total

    asyncio.run(scenario())


def test_get_navigation_never_writes_and_initialization_is_the_only_change(
    intake_app: ModuleType,
) -> None:
    import asyncio

    async def scenario() -> None:
        await _register_release(intake_app)
        paths = [
            ROOT,
            f"/commercial?bid_id={BID_ID}",
            f"/bids/{BID_ID}",
            f"/decisions?bid_id={BID_ID}",
        ]
        before = _counts(intake_app)
        pages = [await _page(intake_app, p) for p in paths]
        assert _counts(intake_app) == before
        assert "never creates them" not in pages[0]  # no commercial blocker yet
        assert "Initializing is an explicit action" in pages[1]

        status, _, body = await request(
            intake_app.app,
            "POST",
            "/commercial",
            body=urlencode(
                {"bid_id": BID_ID, "operation": "initialize", "default_owner": "Owner"}
            ).encode(),
        )
        assert status == 303, body[:300]
        after = _counts(intake_app)
        assert after["commercial_positions"] > before["commercial_positions"]
        assert after["audit_log"] > before["audit_log"]

        # Navigating again changes nothing further, and the new blocker is explained.
        pages = [await _page(intake_app, p) for p in paths]
        assert _counts(intake_app) == after
        assert "commercial position(s) remain unresolved" in pages[0]
        assert "This is not a Package Intake step" in pages[0]

    asyncio.run(scenario())


def test_unreviewed_release_still_cannot_be_confirmed_into_the_bid_basis(
    intake_app: ModuleType,
) -> None:
    import asyncio

    async def scenario() -> None:
        await _register_release(intake_app)
        release_id = next(
            a
            for a in intake_app.bid_package_service.summary(BID_ID).attention
            if a.code == "RECEIVED_RELEASE_NOT_IN_BASIS"
        ).record_id
        before = _counts(intake_app)
        await request(
            intake_app.app,
            "POST",
            f"{ROOT}/basis-snapshots",
            body=urlencode(
                {
                    "operation_id": "op-1",
                    "expected_current_snapshot_id": "",
                    "release_ids": release_id or "",
                    "label": "Bid Basis 1",
                }
            ).encode(),
        )
        assert _counts(intake_app)["bid_basis_snapshots"] == before["bid_basis_snapshots"]

    asyncio.run(scenario())
