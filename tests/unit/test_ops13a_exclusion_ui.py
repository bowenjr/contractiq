"""The exclusion-reason failure is an interaction on the release's file review, not a dead end."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from urllib.parse import urlencode

import pytest

from scripts.asgi_acceptance_ops08 import request
from tests.unit.test_bid_package_governing_ui import (
    _register_release,
    _tokens,
)
from tests.unit.test_bid_package_governing_ui import (
    governing_ui_app as _governing_ui_app,
)

BULK_MESSAGE = b"Enter why these selected files are excluded, or choose Include."
SINGLE_MESSAGE = b"Enter why this file is excluded, or choose Include."


@pytest.fixture
def exclusion_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    yield from _governing_ui_app.__wrapped__(tmp_path, monkeypatch)


def _disposition_events(app: ModuleType) -> int:
    with app.db._conn() as connection:
        return int(
            connection.execute(
                "SELECT count(*) FROM bid_received_file_disposition_events"
            ).fetchone()[0]
        )


async def _release_page(app: ModuleType, release_path: str) -> bytes:
    status, _, page = await request(app.app, "GET", release_path)
    assert status == 200
    return page


def test_bulk_exclusion_without_reason_keeps_files_selection_and_points_at_the_field(
    exclusion_app: ModuleType,
) -> None:
    async def scenario() -> None:
        app = exclusion_app
        release_path = await _register_release(app)
        tokens = _tokens(await _release_page(app, release_path), "review_tokens")
        chosen = sorted(tokens)[:3]
        before = _disposition_events(app)
        pairs = [
            ("operation_id", "exclude-no-reason"),
            ("analysis_eligibility", "EXCLUDED"),
            ("exclusion_reason", ""),
            ("content_form", "TEXTUAL"),
        ]
        pairs += [("file_ids", file_id) for file_id in chosen]
        pairs += [("review_tokens", f"{file_id}|{tokens[file_id]}") for file_id in chosen]
        status, _, body = await request(
            app.app,
            "POST",
            f"{release_path}/bulk-dispositions",
            body=urlencode(pairs).encode(),
        )
        assert status == 422
        assert BULK_MESSAGE in body
        # The release file review is rendered, not the import-folder picker.
        assert b"Select the received customer package folder" not in body
        assert b'id="files"' in body
        assert body.count(b'class="row intake-file"') >= 16
        # No orphaned panel of raw enum values.
        assert b"Analysis eligibility" not in body
        assert b"<dt>Content form</dt>" not in body
        # The selection and the labelled, invalid reason field are retained.
        assert len(re.findall(rb"data-folder-file checked", body)) == len(chosen)
        assert b'id="bulk-exclusion-reason"' in body
        assert b'aria-invalid="true"' in body
        assert b'href="#bulk-exclusion-reason"' in body
        # Backend validation is intact: nothing was written.
        assert _disposition_events(app) == before

        pairs = [
            (key, "ELIGIBLE" if key == "analysis_eligibility" else value) for key, value in pairs
        ]
        status, headers, _ = await request(
            app.app,
            "POST",
            f"{release_path}/bulk-dispositions",
            body=urlencode(pairs).encode(),
        )
        assert status == 303
        assert "notice=analysis_included" in headers["location"]

    import asyncio

    asyncio.run(scenario())


def test_single_file_exclusion_without_reason_reopens_that_file_with_its_choice(
    exclusion_app: ModuleType,
) -> None:
    async def scenario() -> None:
        app = exclusion_app
        release_path = await _register_release(app)
        tokens = _tokens(await _release_page(app, release_path), "review_tokens")
        file_id = sorted(tokens)[0]
        before = _disposition_events(app)
        entered = {
            "release_id": release_path.rsplit("/", 1)[-1],
            "supersedes_event_id": tokens[file_id],
            "operation_id": "single-exclude-no-reason",
            "content_form": "TEXTUAL",
            "analysis_eligibility": "EXCLUDED",
            "exclusion_reason": "",
        }
        bid_root = "/".join(release_path.split("/")[:2] + [release_path.split("/")[2]])
        path = f"{bid_root}/package-intake-addenda/files/{file_id}/dispositions"
        status, _, body = await request(app.app, "POST", path, body=urlencode(entered).encode())
        assert status == 422
        assert SINGLE_MESSAGE in body
        assert b"Select the received customer package folder" not in body
        assert body.count(b'class="row intake-file"') >= 16
        assert f'id="reason-{file_id}"'.encode() in body
        assert b'aria-invalid="true"' in body
        assert f'href="#reason-{file_id}"'.encode() in body
        assert b'<details class="row intake-file" open>' in body
        assert re.search(rb'<option value="EXCLUDED" selected>', body)
        assert _disposition_events(app) == before

        entered["analysis_eligibility"] = "ELIGIBLE"
        status, headers, _ = await request(app.app, "POST", path, body=urlencode(entered).encode())
        assert status == 303
        assert headers["location"].endswith("#files")
        assert _disposition_events(app) == before + 1

    import asyncio

    asyncio.run(scenario())
