"""Focused HTTP and markup checks for the received-file review flow."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from scripts.asgi_acceptance_ops08 import request
from tests.unit.test_bid_package_governing_ui import (
    BID_ID,
    _register_release,
    _tokens,
)
from tests.unit.test_bid_package_governing_ui import (
    governing_ui_app as _governing_ui_app,
)


@pytest.fixture
def review_flow_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    yield from _governing_ui_app.__wrapped__(tmp_path, monkeypatch)


async def _post(
    app: ModuleType, path: str, pairs: list[tuple[str, str]]
) -> tuple[int, dict[str, str], bytes]:
    return await request(app.app, "POST", path, body=urlencode(pairs).encode())


def _row_counts(app: ModuleType) -> tuple[int, ...]:
    tables = (
        "bid_received_file_disposition_events",
        "bid_received_file_governing_events",
        "bid_received_file_document_links",
        "documents",
        "document_versions",
    )
    with app.db._conn() as connection:
        return tuple(
            int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in tables
        )


def _assert_notice_redirect(location: str, release_path: str, code: str, count: int) -> None:
    parsed = urlsplit(location)
    assert parsed.path == release_path
    assert parsed.fragment == "files"
    query = parse_qs(parsed.query)
    changed = query.pop("changed", None)  # actual-change count, reported separately
    assert query == {"notice": [code], "count": [str(count)]}
    assert changed is None or 0 <= int(changed[0]) <= count
    assert location.endswith("#files")


def test_empty_bulk_selections_redirect_without_writing_rows(
    review_flow_app: ModuleType,
) -> None:
    async def scenario() -> None:
        app = review_flow_app
        release_path = await _register_release(app)
        before = _row_counts(app)
        posts = (
            (
                "bulk-dispositions",
                [("operation_id", "empty-review"), ("analysis_eligibility", "ELIGIBLE")],
            ),
            (
                "bulk-governing-sources",
                [
                    ("operation_id", "empty-governing"),
                    ("governing_disposition", "GOVERNING"),
                ],
            ),
            (
                "bulk-controlled-documents",
                [("category", "SOLICITATION"), ("version_label", "Original")],
            ),
        )
        for suffix, pairs in posts:
            status, headers, body = await _post(app, f"{release_path}/{suffix}", pairs)
            assert status == 303
            assert b"Tuple" not in body
            _assert_notice_redirect(headers["location"], release_path, "none_selected", 0)
            status, _, page = await request(app.app, "GET", headers["location"])
            assert status == 200
            assert b"Tuple" not in page
            assert (
                b"No files were selected. Tick one or more files, or use "
                b"&#39;Select all files in this folder&#39;, then try again."
            ) in page
        assert _row_counts(app) == before

    asyncio.run(scenario())


def test_review_success_notice_safe_query_and_ready_page_controls(
    review_flow_app: ModuleType,
) -> None:
    async def scenario() -> None:
        app = review_flow_app
        release_path = await _register_release(app)
        status, _, page = await request(app.app, "GET", release_path)
        assert status == 200
        review_tokens = _tokens(page, "review_tokens")
        pairs = [("operation_id", "include-all"), ("analysis_eligibility", "ELIGIBLE")]
        for file_id, event_id in review_tokens.items():
            pairs.extend([("file_ids", file_id), ("review_tokens", f"{file_id}|{event_id}")])

        status, headers, body = await _post(app, f"{release_path}/bulk-dispositions", pairs)
        assert status == 303, body[:500]
        _assert_notice_redirect(
            headers["location"], release_path, "analysis_included", len(review_tokens)
        )
        status, _, page = await request(app.app, "GET", headers["location"])
        assert status == 200
        assert (
            f"Included {len(review_tokens)} file(s) for analysis: "
            f"{len(review_tokens)} changed, 0 already set. "
            "0 file(s) still need a review decision."
        ).encode() in page
        assert b'class="bulk-review-bar"' in page
        assert (
            f"data-bulk-selected-count>0 of {len(review_tokens)} file(s) selected".encode() in page
        )
        assert b'type="submit" form="bulk-review" data-bulk-submit disabled' in page
        assert (
            b"Optional: choose governing Bid Basis sources (not needed for analysis or discovery)"
        ) in page
        assert b"data-governing-selected-count>0 file(s) selected" in page
        assert b"data-governing-submit disabled" in page
        assert (
            f'href="/bids/{BID_ID}/deliverable-discovery">Run deliverables discovery'.encode()
            in page
        )

        governing_tokens = _tokens(page, "governing_tokens")
        file_id, event_id = next(iter(governing_tokens.items()))
        status, headers, _ = await _post(
            app,
            f"{release_path}/bulk-governing-sources",
            [
                ("operation_id", "select-one-governing"),
                ("governing_disposition", "GOVERNING"),
                ("file_ids", file_id),
                ("governing_tokens", f"{file_id}|{event_id}"),
            ],
        )
        assert status == 303
        _assert_notice_redirect(headers["location"], release_path, "governing_selected", 1)
        status, _, page = await request(app.app, "GET", headers["location"])
        assert status == 200
        assert (
            b"Selected 1 file(s) as governing Bid Basis sources: 1 changed, 0 already set." in page
        )

        hostile = f"{release_path}?notice=%3Cscript%3Ealert(1)%3C/script%3E&count=1"
        status, _, page = await request(app.app, "GET", hostile)
        assert status == 200
        assert b"alert(1)" not in page
        assert b"&lt;script&gt;" not in page

    asyncio.run(scenario())
