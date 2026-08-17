"""Dependency-free in-process ASGI acceptance for OPS-02."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID


async def request(app: Any, path: str) -> tuple[int, bytes]:
    sent: list[dict[str, Any]] = []
    messages = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    parsed = urlsplit(path)
    await app(
        {
            "type": "http",
            "method": "GET",
            "path": parsed.path,
            "query_string": parsed.query.encode(),
            "headers": [],
            "scheme": "http",
            "server": ("127.0.0.1", 0),
            "client": ("127.0.0.1", 1),
            "http_version": "1.1",
        },
        receive,
        send,
    )
    status = next(
        int(message["status"]) for message in sent if message["type"] == "http.response.start"
    )
    payload = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return status, payload


async def form_request(
    app: Any,
    path: str,
    form: dict[str, str],
) -> tuple[int, dict[str, str], bytes]:
    sent: list[dict[str, Any]] = []
    body = urlencode(form).encode()
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    parsed = urlsplit(path)
    await app(
        {
            "type": "http",
            "method": "POST",
            "path": parsed.path,
            "query_string": parsed.query.encode(),
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
            "scheme": "http",
            "server": ("127.0.0.1", 0),
            "client": ("127.0.0.1", 1),
            "http_version": "1.1",
        },
        receive,
        send,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    headers = {
        bytes(key).decode().lower(): bytes(value).decode()
        for key, value in start.get("headers", [])
    }
    payload = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return int(start["status"]), headers, payload


async def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="contractiq-ops02-asgi-") as directory:
        os.environ["CONTRACTIQ_DB_PATH"] = str(Path(directory) / "app.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(Path(directory) / "documents")
        import app
        from core.enums import BidLevel, CustomerType
        from core.schemas import Bid

        as_of = date(2026, 8, 17)
        now = datetime(2026, 8, 17, 12, tzinfo=UTC)
        app._working_date = lambda: as_of
        ids = iter(UUID(int=value) for value in range(1, 100))
        app.work_item_service._now_factory = lambda: now
        app.work_item_service._id_factory = lambda: next(ids)
        bid = Bid(
            bid_id="B-2026-0001",
            customer="Synthetic customer",
            customer_type=CustomerType.EPC,
            project_name="Synthetic bid context",
            sales_owner="Synthetic sales",
            bc_owner="Synthetic coordinator",
            release_date=date(2026, 8, 1),
            customer_due_date=date(2026, 8, 31),
            internal_due_date=date(2026, 8, 28),
            estimated_value=Decimal("1000"),
            classification=BidLevel.LEVEL_1,
            created_at=now,
            updated_at=now,
        )
        app.bid_repository.create_bid(bid)
        blocked = app.work_item_service.create_work_item(
            {
                "bid_id": bid.bid_id,
                "title": "Blocked bid work",
                "status": "BLOCKED",
                "blocker_note": "Synthetic blocker",
                "blocker_description": "Synthetic blocker",
                "resolution_owner": "Synthetic owner",
            },
            "acceptance",
        )
        overdue = app.work_item_service.create_work_item(
            {"title": "Standalone overdue", "next_action_date": "2026-08-16"},
            "acceptance",
        )
        app.work_item_service.create_work_item(
            {"title": "Due today", "due_date": "2026-08-17"}, "acceptance"
        )
        app.work_item_service.create_work_item(
            {
                "title": "Waiting follow-up",
                "status": "WAITING",
                "waiting_on": "Supplier",
                "waiting_party_label": "Supplier",
                "waiting_owed": "Response",
                "chase_date": "2026-08-17",
            },
            "acceptance",
        )
        app.work_item_service.create_work_item(
            {"title": "Upcoming", "due_date": "2026-08-20"}, "acceptance"
        )
        completed_source = app.work_item_service.create_work_item(
            {"title": "Completed history"}, "acceptance"
        )
        completed = app.work_item_service.transition_work_item(
            completed_source.work_item_id,
            {
                "expected_version": completed_source.version,
                "status": "COMPLETED",
                "completion_outcome": "Complete",
            },
            "acceptance",
        )
        cancelled_source = app.work_item_service.create_work_item(
            {"title": "Cancelled history"}, "acceptance"
        )
        app.work_item_service.transition_work_item(
            cancelled_source.work_item_id,
            {
                "expected_version": cancelled_source.version,
                "status": "CANCELLED",
                "cancellation_reason": "Cancelled",
            },
            "acceptance",
        )

        create_status, _, _ = await form_request(
            app.app,
            "/my-work",
            {
                "title": "Browser-form persistence",
                "category": "CUSTOMER_REQUEST",
                "next_action_date": "2026-08-18",
            },
        )
        assert create_status == 303
        browser_item = next(
            item
            for item in app.work_item_repository.list()
            if item.title == "Browser-form persistence"
        )
        editor_status, editor_html = await request(app.app, f"/my-work/{browser_item.work_item_id}")
        assert editor_status == 200
        assert b'name="priority"' in editor_html
        assert b'name="contribution_candidate"' in editor_html
        assert b'data-status-section="WAITING" hidden disabled' in editor_html
        browser_audit_before = len(app.bid_repository.list_audit(None))
        save_status, save_headers, _ = await form_request(
            app.app,
            f"/my-work/{browser_item.work_item_id}",
            {
                "expected_version": str(browser_item.version),
                "title": "Browser-form persistence",
                "details": "Structured waiting round trip",
                "category": "CUSTOMER_REQUEST",
                "responsibility_domain": "CUSTOMER_SOLUTION",
                "priority": "HIGH",
                "status": "WAITING",
                "bid_id": bid.bid_id,
                "due_date": "2026-08-25",
                "next_action_date": "2026-08-19",
                "contribution_candidate": "true",
                "waiting_party_kind": "CUSTOMER",
                "waiting_party_label": "Customer procurement",
                "waiting_owed": "Signed clarification response",
                "requested_date": "2026-08-17",
                "chase_date": "2026-08-20",
            },
        )
        assert save_status == 303
        stored_browser_item = app.work_item_repository.get(browser_item.work_item_id)
        assert stored_browser_item is not None
        assert stored_browser_item.waiting_party_label == "Customer procurement"
        assert stored_browser_item.waiting_owed == "Signed clarification response"
        assert stored_browser_item.chase_date == date(2026, 8, 20)
        assert stored_browser_item.due_date == date(2026, 8, 25)
        assert stored_browser_item.next_action_date == date(2026, 8, 19)
        assert stored_browser_item.contribution_candidate is True
        assert len(app.bid_repository.list_audit(None)) == browser_audit_before + 1
        reload_status, reload_html = await request(app.app, save_headers["location"])
        assert reload_status == 200
        assert b'value="Customer procurement"' in reload_html
        assert b"Signed clarification response" in reload_html
        assert b'value="2026-08-20"' in reload_html
        assert b'data-status-section="WAITING"><legend>Waiting details</legend>' in reload_html
        versions_before = {
            item.work_item_id: item.version for item in app.work_item_repository.list()
        }
        audit_before = len(app.bid_repository.list_audit(None))

        status, my_day = await request(app.app, "/my-day")
        assert status == 200
        assert b"/my-work#quick-capture" in my_day
        assert f"/my-work/{blocked.work_item_id}".encode() in my_day
        assert f"/my-work/{completed.work_item_id}?register_view=history".encode() in my_day
        assert b'id="create-form"' not in my_day and b"fetch(" not in my_day

        status, current = await request(app.app, "/my-work")
        assert status == 200 and overdue.title.encode() in current
        assert b"Standalone work" in current
        status, history = await request(app.app, "/my-work?view=history")
        assert status == 200 and completed.title.encode() in history
        status, bid_context = await request(
            app.app, f"/my-work?view=all&context=bid&bid_id={bid.bid_id}"
        )
        assert status == 200 and bid.project_name.encode() in bid_context
        status, invalid = await request(app.app, f"/my-work?context=standalone&bid_id={bid.bid_id}")
        assert status == 422 and b"bid_id is only valid" in invalid

        for path in (
            "/requirements",
            "/scope-interfaces",
            "/suppliers",
            "/deliverables",
            "/commercial",
            "/contract-risks",
            "/decisions",
            "/commercial-scenarios",
            "/negotiations",
            "/proposals",
        ):
            route_status, body = await request(app.app, path)
            assert route_status == 200 and b"Traceback" not in body, path

        assert versions_before == {
            item.work_item_id: item.version for item in app.work_item_repository.list()
        }
        assert len(app.bid_repository.list_audit(None)) == audit_before
    print("OPS-02 ASGI acceptance: PASS")


if __name__ == "__main__":
    asyncio.run(main())
