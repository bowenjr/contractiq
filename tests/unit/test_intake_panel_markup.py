"""Rendered acceptance checks for the received-file bulk review controls."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from tests.unit.test_bid_package_governing_ui import (
    _files_by_name,
    _review,
)
from tests.unit.test_package_intake_separation import (
    BID_ID,
    _page,
    _register_release,
    intake_app,
)


def _assert_balanced_javascript(source: str) -> None:
    """Check delimiters while ignoring quoted JavaScript text."""
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    quote: str | None = None
    escaped = False
    for char in source:
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in "([{":
            stack.append(char)
        elif char in ")]}":
            assert stack and stack.pop() == pairs[char]
    assert quote is None
    assert stack == []


def test_bulk_panel_exposes_compact_zero_selection_controls(intake_app) -> None:
    async def scenario() -> None:
        release_path = await _register_release(intake_app)
        page = await _page(intake_app, release_path)

        assert "data-bulk-fields" in page
        assert "data-bulk-guidance" in page
        assert (
            "Select files below to review them. Tick files, or use 'Select all files in this "
            "folder' or 'Select all files'."
        ) in page
        assert "data-bulk-reason-error" in page
        assert "Enter why these selected files are excluded, or choose Include." in page
        assert "data-select-all-files" in page
        assert "data-clear-all-files" in page
        assert "Optional file kind for all selected files" in page
        assert "data-folder-selected-count" in page
        assert re.search(
            r"<summary>.*data-select-folder.*data-clear-folder.*</summary>", page, re.S
        )
        assert re.search(r"data-bulk-submit disabled", page)
        assert 'id="governing-sources"' in page
        assert f'href="/documents?bid_id={BID_ID}"' not in page

        scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
        assert scripts
        for script in scripts:
            _assert_balanced_javascript(script)

    asyncio.run(scenario())


def test_hidden_attribute_has_an_unconditional_stylesheet_rule() -> None:
    stylesheet = (Path(__file__).parents[2] / "static" / "style.css").read_text()
    assert "[hidden]{display:none !important}" in stylesheet


def test_release_continue_link_follows_the_guided_current_task(intake_app) -> None:
    """The release page offers one forward action: the guided current task's, not its own."""

    async def scenario() -> None:
        release_path = await _register_release(intake_app)
        release_id = release_path.rsplit("/", 1)[-1]
        files = _files_by_name(intake_app, BID_ID, release_id)
        file_ids = [str(row["file_id"]) for row in files.values()]

        page = await _page(intake_app, release_path)
        assert 'aria-label="Continue to controlled customer documents"' in page
        assert "Continue: Choose files for analysis</a>" in page
        assert "Confirm Bid Basis</a>" not in page

        await _review(intake_app, release_path, file_ids, "ELIGIBLE", "review-all")
        page = await _page(intake_app, release_path)
        assert "Continue: Run deliverables discovery</a>" in page
        assert page.count('aria-label="Guided Bid workflow"') == 1

    asyncio.run(scenario())


def test_workspace_lists_unassessed_facts_without_whole_bid_clear_claim(intake_app) -> None:
    async def scenario() -> None:
        await _register_release(intake_app)
        workspace = await _page(intake_app, f"/bids/{BID_ID}")
        bids = await _page(intake_app, "/bids")
        my_day = await _page(intake_app, "/my-day")

        assert "Whole-Bid readiness gate" not in workspace
        assert "Bid is CLEAR" not in workspace
        assert "Development gate check:" in workspace
        assert re.search(r"Not yet assessed:[^<]*requirements[^<]*scope", workspace)
        assert "Development gate check:" in bids
        assert "Development checks" in my_day

    asyncio.run(scenario())


__all__ = ["intake_app"]
