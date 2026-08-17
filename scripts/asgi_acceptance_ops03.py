"""Dependency-free HTML-form ASGI acceptance for OPS-03."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID


async def request(
    application: Any,
    method: str,
    path: str,
    *,
    form: dict[str, str | list[str]] | None = None,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    sent: list[dict[str, Any]] = []
    if payload is not None:
        body = json.dumps(payload).encode()
        headers = [(b"content-type", b"application/json")]
    else:
        body = urlencode(form or {}, doseq=True).encode()
        headers = (
            [(b"content-type", b"application/x-www-form-urlencoded")] if method == "POST" else []
        )
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
            "headers": headers,
            "scheme": "http",
            "server": ("127.0.0.1", 0),
            "client": ("127.0.0.1", 1),
            "http_version": "1.1",
        },
        receive,
        send,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    response_headers = {
        bytes(key).decode().lower(): bytes(value).decode()
        for key, value in start.get("headers", [])
    }
    response_body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return int(start["status"]), response_headers, response_body


def complete(title: str, start: str) -> dict[str, str | list[str]]:
    return {
        "title": title,
        "organization": "Synthetic organization",
        "mission": "Deliver controlled bids and contracts outcomes.",
        "boundaries": "No silent commercial authority.",
        "coordination": "Coordinate sales, suppliers, and governance.",
        "outcomes": "Auditable decisions and complete offers.",
        "cadence": "Daily attention and weekly portfolio review.",
        "domains": ["CUSTOMER_SOLUTION", "RISK_ASSURANCE"],
        "effective_from": start,
        "effective_until": "",
    }


async def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="contractiq-ops03-asgi-") as directory:
        os.environ["CONTRACTIQ_DB_PATH"] = str(Path(directory) / "app.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(Path(directory) / "documents")
        import app

        app._working_date = lambda: date(2026, 9, 1)
        app.role_profile_service._now_factory = lambda: datetime(2026, 8, 17, 14, tzinfo=UTC)
        ids = iter(UUID(int=value) for value in range(1, 500))
        app.role_profile_service._id_factory = lambda: next(ids)

        status, _, landing = await request(app.app, "GET", "/role-framework")
        assert status == 200 and b"Role framework setup required" in landing
        status, headers, _ = await request(
            app.app,
            "POST",
            "/role-framework",
            form={"title": "ASGI draft", "effective_from": "2026-09-01"},
        )
        assert status == 303
        parent_path = headers["location"]
        parent_id = parent_path.rsplit("/", 1)[-1]
        parent = app.role_profile_service.get_profile(parent_id)
        assert parent.title == "ASGI draft" and parent.mission == ""
        status, _, draft_html = await request(app.app, "GET", parent_path)
        assert status == 200 and b'value="ASGI draft"' in draft_html

        status, _, rejected_html = await request(
            app.app,
            "POST",
            f"{parent_path}/publish",
            form={"expected_version_token": parent.version_token},
        )
        assert status == 422 and b"non-empty mission" in rejected_html
        assert app.role_profile_service.get_profile(parent_id) == parent

        status, headers, _ = await request(
            app.app,
            "POST",
            parent_path,
            form={
                **complete("ASGI parent", "2026-09-01"),
                "expected_version_token": parent.version_token,
            },
        )
        assert status == 303 and headers["location"] == parent_path
        parent = app.role_profile_service.get_profile(parent_id)
        status, _, _ = await request(
            app.app,
            "POST",
            f"{parent_path}/publish",
            form={"expected_version_token": parent.version_token},
        )
        assert status == 303
        parent = app.role_profile_service.get_profile(parent_id)

        status, headers, _ = await request(
            app.app,
            "POST",
            f"{parent_path}/revise",
            form={"expected_version_token": parent.version_token},
        )
        assert status == 303
        child_path = headers["location"]
        child_id = child_path.rsplit("/", 1)[-1]
        child = app.role_profile_service.get_profile(child_id)
        parent = app.role_profile_service.get_profile(parent_id)
        status, _, _ = await request(
            app.app,
            "POST",
            child_path,
            form={
                **complete("ASGI child", "2026-10-01"),
                "expected_version_token": child.version_token,
            },
        )
        assert status == 303
        child = app.role_profile_service.get_profile(child_id)
        audit_before_publish = len(app.bid_repository.list_audit(None))
        status, headers, _ = await request(
            app.app,
            "POST",
            f"{child_path}/publish",
            form={
                "expected_version_token": child.version_token,
                "parent_expected_version_token": parent.version_token,
            },
        )
        assert status == 303 and headers["location"] == child_path
        assert len(app.bid_repository.list_audit(None)) == audit_before_publish + 2
        stored_parent = app.role_profile_service.get_profile(parent_id)
        stored_child = app.role_profile_service.get_profile(child_id)
        assert stored_parent.effective_until == date(2026, 9, 30)
        assert stored_child.state.value == "PUBLISHED"
        before_changeover = app.role_profile_service.effective_profile(as_of=date(2026, 9, 30))
        at_changeover = app.role_profile_service.effective_profile(as_of=date(2026, 10, 1))
        assert before_changeover is not None and before_changeover.profile_id == parent_id
        assert at_changeover is not None and at_changeover.profile_id == child_id

        status, _, reloaded = await request(app.app, "GET", child_path)
        assert status == 200
        assert b"ASGI child" in reloaded
        assert b"Role Profile Superseded" in reloaded
        assert b"Human-authored by" in reloaded
        assert f'href="/role-framework/{parent_id}"'.encode() in reloaded

        audit_before_stale = app.bid_repository.list_audit(None)
        status, _, stale_html = await request(
            app.app,
            "POST",
            f"{child_path}/retire",
            form={"expected_version_token": "stale"},
        )
        assert status == 422 and b"Stale role profile token" in stale_html
        assert app.role_profile_service.get_profile(child_id) == stored_child
        assert app.bid_repository.list_audit(None) == audit_before_stale

        status, _, api_body = await request(app.app, "GET", "/api/ops/role-profiles")
        assert status == 200
        api_payload = json.loads(api_body)
        assert len(api_payload["profiles"]) == 2
        assert api_payload["effective"]["profile_id"] == parent_id

        profiles_before = app.role_profile_service.list_profiles()
        audit_before_get = app.bid_repository.list_audit(None)
        for path in ("/role-framework", parent_path, child_path):
            status, _, _ = await request(app.app, "GET", path)
            assert status == 200
        assert app.role_profile_service.list_profiles() == profiles_before
        assert app.bid_repository.list_audit(None) == audit_before_get
    print("OPS-03 ASGI acceptance: PASS")


if __name__ == "__main__":
    asyncio.run(main())
