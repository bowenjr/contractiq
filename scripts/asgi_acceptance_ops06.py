"""Dependency-free browser acceptance for the OPS-06 operating shell."""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit


async def request(
    application: Any,
    method: str,
    path: str,
    form: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    sent: list[dict[str, Any]] = []
    body = urlencode(form or {}).encode()
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    parsed = urlsplit(path)
    await application(
        {
            "type": "http",
            "method": method,
            "path": parsed.path,
            "query_string": parsed.query.encode(),
            "headers": (
                [(b"content-type", b"application/x-www-form-urlencoded")]
                if method == "POST"
                else []
            ),
            "scheme": "http",
            "server": ("127.0.0.1", 0),
            "client": ("127.0.0.1", 1),
            "http_version": "1.1",
        },
        receive,
        send,
    )
    start = next(item for item in sent if item["type"] == "http.response.start")
    headers = {
        bytes(key).decode().lower(): bytes(value).decode()
        for key, value in start.get("headers", [])
    }
    response_body = b"".join(
        item.get("body", b"") for item in sent if item["type"] == "http.response.body"
    )
    return int(start["status"]), headers, response_body


async def _create_bid(application: Any, level: str, title: str) -> str:
    estimated_value = "50000" if level == "level_1" else "1000000"
    customer_type = "end_user" if level == "level_1" else "epcm"
    status, headers, _ = await request(
        application,
        "POST",
        "/bids",
        {
            "project_name": title,
            "customer": "Example EPCM",
            "customer_type": customer_type,
            "sales_owner": "Sales Lead",
            "bc_owner": "Jason",
            "release_date": "2026-08-20",
            "customer_due_date": "2026-09-30",
            "internal_due_date": "2026-09-25",
            "estimated_value": estimated_value,
            "currency": "CAD",
            "classification": level,
        },
    )
    assert status == 303
    return headers["location"].rsplit("/", 1)[-1]


async def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="contractiq-ops06-asgi-") as directory:
        os.environ["CONTRACTIQ_DB_PATH"] = str(Path(directory) / "app.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(Path(directory) / "documents")
        original_connection = socket.create_connection

        def blocked(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("OPS-06 attempted a network connection")

        socket.create_connection = blocked
        try:
            import app

            status, _, home = await request(app.app, "GET", "/")
            assert status == 200 and b"<h1>My Day</h1>" in home
            for path in (b"/my-day", b"/bids", b"/my-work", b"/reports-center", b"/administration"):
                assert b'href="' + path + b'"' in home
            assert b'href="/vendor-documents"' not in home

            status, _, bids_page = await request(app.app, "GET", "/bids")
            assert status == 200 and b"Create Bid" in bids_page
            assert b'value="escalate"' not in bids_page
            level_one = await _create_bid(app.app, "level_1", "Level one Bid")
            level_three = await _create_bid(app.app, "level_3", "Level three Bid")
            sections = (
                "",
                "/requirements-scope",
                "/manufacturers-coverage",
                "/commercial-contract",
                "/proposal-negotiation",
                "/award-handover",
            )
            before_one = app.bid_repository.get_bid(level_one)
            before_audit = app.bid_repository.list_audit(level_one)
            for suffix in sections:
                status, _, page = await request(app.app, "GET", f"/bids/{level_one}{suffix}")
                assert status == 200
                assert level_one.encode() in page
                assert b"Example EPCM" in page
                assert b"Gate and readiness rail" in page
                assert b"Next required action" in page
                assert page.count(b"Bid workspace sections") == 1
            assert app.bid_repository.get_bid(level_one) == before_one
            assert app.bid_repository.list_audit(level_one) == before_audit

            _, _, one = await request(app.app, "GET", f"/bids/{level_one}")
            _, _, three = await request(app.app, "GET", f"/bids/{level_three}")
            assert b"Not required for Level 0 or Level 1" in one
            assert b"Required for Level 2, Level 3 and Level 4" in three
            _, _, manufacturers = await request(
                app.app, "GET", f"/bids/{level_three}/manufacturers-coverage"
            )
            assert b"Vendor Document Requirements" in manufacturers
            assert b"post-award" not in manufacturers.lower()

            status, _, _ = await request(app.app, "GET", "/bids/B-MISSING")
            assert status == 404
            before_bids = app.bid_repository.list_bids()
            before_all_audit = app.bid_repository.list_audit()
            status, headers, invalid = await request(
                app.app,
                "GET",
                "/bids?view=nonsense&owner=Jason&status=active",
            )
            assert status == 422
            assert "text/html" in headers["content-type"]
            assert b"Could not apply portfolio filters" in invalid
            assert b"Invalid selection: nonsense" in invalid
            assert b'name="owner" value="Jason"' in invalid
            assert b'value="active" selected' in invalid
            assert b'{"detail"' not in invalid
            status, headers, invalid = await request(
                app.app,
                "GET",
                "/bids?classification=level_3&deadline=sideways",
            )
            assert status == 422 and "text/html" in headers["content-type"]
            assert b"Invalid selection: sideways" in invalid
            assert b'value="level_3" selected' in invalid
            status, headers, invalid = await request(
                app.app,
                "GET",
                "/bids?view=history&status=active&owner=Jason",
            )
            assert status == 422 and "text/html" in headers["content-type"]
            assert b"History view cannot be combined" in invalid
            assert b'name="owner" value="Jason"' in invalid
            assert app.bid_repository.list_bids() == before_bids
            assert app.bid_repository.list_audit() == before_all_audit

            register_returns = (
                ("/requirements", "requirements-scope"),
                ("/documents", "requirements-scope"),
                ("/scope-interfaces", "requirements-scope"),
                ("/suppliers", "manufacturers-coverage"),
                ("/commercial", "commercial-contract"),
                ("/contract-risks", "commercial-contract"),
                ("/decisions", "commercial-contract"),
                ("/deliverables", "proposal-negotiation"),
                ("/proposals", "proposal-negotiation"),
                ("/negotiations", "proposal-negotiation"),
            )
            for register, section in register_returns:
                status, _, page = await request(
                    app.app,
                    "GET",
                    f"{register}?bid_id={level_three}",
                )
                assert status == 200
                assert f'href="/bids/{level_three}/{section}"'.encode() in page
                assert f"Back to Bid {level_three}".encode() in page
            status, _, admin = await request(app.app, "GET", "/administration")
            assert status == 200 and b"Role Framework" in admin and b"Knowledge" in admin
            status, _, reports = await request(app.app, "GET", "/reports-center")
            assert status == 200 and b"Only currently supported exports" in reports
        finally:
            socket.create_connection = original_connection
    print("OPS-06 ASGI acceptance: PASS")


if __name__ == "__main__":
    asyncio.run(main())
