import asyncio
import importlib
import json
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import cast
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from core.schemas import Bid


class JsonRequest:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def json(self) -> dict[str, object]:
        return dict(self.payload)


class FormRequest:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def form(self) -> dict[str, object]:
        return dict(self.payload)


def _html(response: HTMLResponse) -> str:
    return bytes(response.body).decode()


def _json(response: JSONResponse) -> dict[str, object]:
    return cast(dict[str, object], json.loads(bytes(response.body)))


async def _asgi_request(
    app: object,
    method: str,
    path: str,
    form: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], str]:
    sent: list[dict[str, object]] = []
    body = urlencode(form or {}).encode()
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict[str, object]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    parsed = urlsplit(path)
    headers = [(b"content-type", b"application/x-www-form-urlencoded")] if method == "POST" else []
    await app(
        {
            "type": "http",
            "method": method,
            "path": parsed.path,
            "query_string": parsed.query.encode(),
            "headers": headers,
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 1),
            "http_version": "1.1",
        },
        receive,
        send,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    response_headers = {
        bytes(key).decode().lower(): bytes(value).decode()
        for key, value in cast(list[tuple[bytes, bytes]], start["headers"])
    }
    response_body = b"".join(
        cast(bytes, message.get("body", b""))
        for message in sent
        if message["type"] == "http.response.body"
    ).decode()
    return int(start["status"]), response_headers, response_body


@pytest.fixture
def ui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "ui.db"))
    sys.modules.pop("app", None)
    app_path = Path(__file__).parents[2] / "app.py"
    spec = importlib.util.spec_from_file_location("app", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load app.py for UI route tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module._working_date = lambda: date(2026, 8, 5)
    return module


def test_empty_my_day_and_existing_navigation_render(ui_app: ModuleType) -> None:
    request = cast(Request, object())

    page = asyncio.run(ui_app.my_day(request))
    dashboard = asyncio.run(ui_app.index(request))
    page_text = _html(page)
    dashboard_text = _html(dashboard)

    assert page.status_code == 200
    assert "Operational view as of" in page_text
    assert "No blocked Bid work items." in page_text
    assert "No control-register blockers require attention." in page_text
    assert 'href="/my-work#quick-capture"' in page_text
    assert 'id="create-form"' not in page_text
    assert "fetch('/api/work-items" not in page_text
    assert dashboard.status_code == 200
    assert 'href="/my-day"' in dashboard_text


def test_ui_api_creates_transitions_completes_and_reopens_audited_item(
    ui_app: ModuleType,
    valid_bid: Bid,
) -> None:
    ui_app.bid_repository.create_bid(valid_bid)

    created_response = asyncio.run(
        ui_app.create_work_item(
            cast(
                Request,
                JsonRequest(
                    {
                        "bid_id": valid_bid.bid_id,
                        "kind": "TASK",
                        "title": "UI work",
                        "priority": "HIGH",
                        "due_date": "2026-08-04",
                        "actor": "jason",
                    }
                ),
            )
        )
    )
    assert created_response.status_code == 201
    created = _json(created_response)
    page = asyncio.run(ui_app.my_day(cast(Request, object())))
    assert "UI work" in _html(page)
    assert "OVERDUE" in _html(page)

    edited_response = asyncio.run(
        ui_app.edit_work_item(
            str(created["work_item_id"]),
            cast(
                Request,
                JsonRequest(
                    {
                        "expected_version": created["version"],
                        "title": "UI work edited",
                        "priority": "CRITICAL",
                        "actor": "jason",
                    }
                ),
            ),
        )
    )
    assert edited_response.status_code == 200
    edited = _json(edited_response)
    assert edited["title"] == "UI work edited"

    waiting_response = asyncio.run(
        ui_app.transition_work_item(
            str(created["work_item_id"]),
            cast(
                Request,
                JsonRequest(
                    {
                        "expected_version": edited["version"],
                        "status": "WAITING",
                        "waiting_on": "Customer counsel",
                        "waiting_party_label": "Customer counsel",
                        "waiting_owed": "Legal response",
                        "chase_date": "2026-08-06",
                        "actor": "jason",
                    }
                ),
            ),
        )
    )
    assert waiting_response.status_code == 200
    waiting = _json(waiting_response)
    assert waiting["waiting_on"] == "Customer counsel"

    completed_response = asyncio.run(
        ui_app.transition_work_item(
            str(created["work_item_id"]),
            cast(
                Request,
                JsonRequest(
                    {
                        "expected_version": waiting["version"],
                        "status": "COMPLETED",
                        "completion_outcome": "Review completed",
                        "actor": "jason",
                    }
                ),
            ),
        )
    )
    assert completed_response.status_code == 200
    completed = _json(completed_response)
    completed_page = asyncio.run(ui_app.my_day(cast(Request, object())))
    assert "Completed and cancelled history (1)" in _html(completed_page)

    reopened_response = asyncio.run(
        ui_app.transition_work_item(
            str(created["work_item_id"]),
            cast(
                Request,
                JsonRequest(
                    {
                        "expected_version": completed["version"],
                        "status": "OPEN",
                        "actor": "jason",
                    }
                ),
            ),
        )
    )
    assert reopened_response.status_code == 200
    reopened = _json(reopened_response)
    assert reopened["completed_at"] is None

    cancelled_response = asyncio.run(
        ui_app.transition_work_item(
            str(created["work_item_id"]),
            cast(
                Request,
                JsonRequest(
                    {
                        "expected_version": reopened["version"],
                        "status": "CANCELLED",
                        "cancellation_reason": "No longer required",
                        "actor": "jason",
                    }
                ),
            ),
        )
    )
    assert cancelled_response.status_code == 200
    assert _json(cancelled_response)["status"] == "CANCELLED"
    assert len(ui_app.bid_repository.list_audit(valid_bid.bid_id)) == 6


def test_ui_validation_error_is_visible_and_does_not_mutate(
    ui_app: ModuleType,
    valid_bid: Bid,
) -> None:
    ui_app.bid_repository.create_bid(valid_bid)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            ui_app.create_work_item(
                cast(
                    Request,
                    JsonRequest(
                        {
                            "bid_id": valid_bid.bid_id,
                            "title": "Waiting without context",
                            "status": "WAITING",
                            "actor": "jason",
                        }
                    ),
                )
            )
        )

    assert raised.value.status_code == 422
    assert "WAITING requires a waiting party" in str(raised.value.detail)
    assert ui_app.work_item_repository.list() == []
    assert ui_app.bid_repository.list_audit(valid_bid.bid_id) == []
    page = asyncio.run(ui_app.my_day(cast(Request, object())))
    assert 'id="status-form"' not in _html(page)


def test_task06_hold_is_read_only_and_my_day_does_not_contact_alice(
    ui_app: ModuleType,
    valid_bid: Bid,
) -> None:
    ui_app.bid_repository.create_bid(valid_bid)
    before_bid = ui_app.bid_repository.get_bid(valid_bid.bid_id)
    before_audit = ui_app.bid_repository.list_audit(valid_bid.bid_id)

    with patch.object(
        ui_app.llm_client,
        "health_check",
        side_effect=AssertionError("My Day attempted an Alice/network call"),
    ) as health_check:
        response = asyncio.run(ui_app.my_day(cast(Request, object())))

    assert response.status_code == 200
    assert "Bid is on HOLD" in _html(response)
    assert valid_bid.bid_id in _html(response)
    assert f'href="/bids/{valid_bid.bid_id}"' in _html(response)
    assert ui_app.bid_repository.get_bid(valid_bid.bid_id) == before_bid
    assert ui_app.bid_repository.list_audit(valid_bid.bid_id) == before_audit
    health_check.assert_not_called()


def test_my_work_quick_capture_and_full_editor_lifecycle(ui_app: ModuleType) -> None:
    page = asyncio.run(ui_app.my_work(cast(Request, object())))
    assert page.status_code == 200
    page_text = _html(page)
    assert '<label for="capture-title">Work title</label>' in page_text
    assert '<label for="capture-category">Work category</label>' in page_text
    assert '<label for="capture-next-action">Next-action date</label>' in page_text
    assert "Opportunity Development" in page_text
    assert ">OPPORTUNITY_DEVELOPMENT<" not in page_text

    created = asyncio.run(
        ui_app.quick_capture_work(
            cast(Request, object()),
            "Prepare regional opportunity summary",
            "OPPORTUNITY_DEVELOPMENT",
            "2026-08-14",
        )
    )
    assert created.status_code == 303
    item = ui_app.work_item_repository.list()[0]
    assert item.bid_id is None
    register = asyncio.run(ui_app.my_work(cast(Request, object())))
    assert f'href="/my-work/{item.work_item_id}"' in _html(register)
    assert "Opportunity Development" in _html(register)

    editor = asyncio.run(ui_app.work_item_detail(cast(Request, object()), item.work_item_id))
    assert editor.status_code == 200
    assert "Prepare regional opportunity summary" in _html(editor)
    assert "Back to My Work" in _html(editor)

    waiting = asyncio.run(
        ui_app.save_work_item_detail(
            cast(
                Request,
                FormRequest(
                    {
                        "expected_version": str(item.version),
                        "title": item.title,
                        "details": "Regional pipeline and actions",
                        "category": item.category.value,
                        "responsibility_domain": "STRATEGIC_OPPORTUNITY",
                        "status": "WAITING",
                        "bid_id": "",
                        "due_date": "2026-08-20",
                        "next_action_date": "2026-08-15",
                        "waiting_party_kind": "INTERNAL_FUNCTION",
                        "waiting_party_label": "Regional sales",
                        "waiting_owed": "Opportunity inputs",
                        "requested_date": "2026-08-12",
                        "chase_date": "2026-08-16",
                        "blocker_description": "",
                        "resolution_owner": "",
                        "review_date": "",
                        "completion_outcome": "",
                        "completion_evidence": "",
                        "cancellation_reason": "",
                    }
                ),
            ),
            item.work_item_id,
        )
    )
    assert waiting.status_code == 303
    saved = ui_app.work_item_repository.get(item.work_item_id)
    assert saved is not None
    assert saved.due_date.isoformat() == "2026-08-20"
    assert saved.next_action_date.isoformat() == "2026-08-15"
    assert saved.responsibility_domain.value == "STRATEGIC_OPPORTUNITY"
    assert saved.waiting_owed == "Opportunity inputs"

    audit_before = len(ui_app.bid_repository.list_audit(None))
    rejected = asyncio.run(
        ui_app.save_work_item_detail(
            cast(
                Request,
                FormRequest(
                    {
                        "expected_version": str(saved.version),
                        "title": "Unsaved title",
                        "category": saved.category.value,
                        "responsibility_domain": "STRATEGIC_OPPORTUNITY",
                        "status": "COMPLETED",
                        "bid_id": "",
                        "due_date": "2026-08-20",
                        "next_action_date": "2026-08-15",
                        "waiting_party_kind": "",
                        "waiting_party_label": "",
                        "waiting_owed": "",
                        "requested_date": "",
                        "chase_date": "",
                        "blocker_description": "",
                        "resolution_owner": "",
                        "review_date": "",
                        "completion_outcome": "",
                        "completion_evidence": "",
                        "cancellation_reason": "",
                    }
                ),
            ),
            item.work_item_id,
        )
    )
    assert rejected.status_code == 422
    assert "Unsaved title" in _html(rejected)
    unchanged = ui_app.work_item_repository.get(item.work_item_id)
    assert unchanged == saved
    assert len(ui_app.bid_repository.list_audit(None)) == audit_before


def test_html_editor_complete_persistence_matrix_uses_real_form_post(
    ui_app: ModuleType,
    valid_bid: Bid,
) -> None:
    ui_app.bid_repository.create_bid(valid_bid)
    create_status, create_headers, _ = asyncio.run(
        _asgi_request(
            ui_app.app,
            "POST",
            "/my-work",
            {
                "title": "HTML waiting round trip",
                "category": "CUSTOMER_REQUEST",
                "next_action_date": "2026-08-18",
            },
        )
    )
    assert create_status == 303
    assert create_headers["location"] == "/my-work"
    created = ui_app.work_item_repository.list()[0]
    open_status, _, open_html = asyncio.run(
        _asgi_request(ui_app.app, "GET", f"/my-work/{created.work_item_id}")
    )
    assert open_status == 200
    assert 'name="waiting_party_label"' in open_html
    assert 'name="priority"' in open_html
    assert 'name="contribution_candidate"' in open_html
    assert 'data-status-section="WAITING" hidden disabled' in open_html
    assert "syncLifecycleSections" in open_html

    audit_before = len(ui_app.bid_repository.list_audit(None))
    save_status, save_headers, _ = asyncio.run(
        _asgi_request(
            ui_app.app,
            "POST",
            f"/my-work/{created.work_item_id}",
            {
                "expected_version": str(created.version),
                "title": "HTML lifecycle round trip",
                "details": "Waiting details",
                "category": "CUSTOMER_REQUEST",
                "responsibility_domain": "CUSTOMER_SOLUTION",
                "priority": "HIGH",
                "status": "WAITING",
                "bid_id": valid_bid.bid_id,
                "due_date": "2026-08-24",
                "next_action_date": "2026-08-19",
                "contribution_candidate": "true",
                "waiting_party_kind": "CUSTOMER",
                "waiting_party_label": "Customer procurement",
                "waiting_owed": "Signed clarification response",
                "requested_date": "2026-08-17",
                "chase_date": "2026-08-20",
            },
        )
    )
    assert save_status == 303
    assert save_headers["location"] == f"/my-work/{created.work_item_id}"

    stored = ui_app.work_item_repository.get(created.work_item_id)
    assert stored is not None
    assert stored.title == "HTML lifecycle round trip"
    assert stored.details == "Waiting details"
    assert stored.category.value == "CUSTOMER_REQUEST"
    assert stored.responsibility_domain is not None
    assert stored.responsibility_domain.value == "CUSTOMER_SOLUTION"
    assert stored.priority.value == "HIGH"
    assert stored.bid_id == valid_bid.bid_id
    assert stored.due_date == date(2026, 8, 24)
    assert stored.next_action_date == date(2026, 8, 19)
    assert stored.contribution_candidate is True
    assert stored.status.value == "WAITING"
    assert stored.waiting_on == "Customer procurement"
    assert stored.waiting_party_label == "Customer procurement"
    assert stored.waiting_owed == "Signed clarification response"
    assert stored.chase_date == date(2026, 8, 20)
    assert len(ui_app.bid_repository.list_audit(None)) == audit_before + 1
    waiting_audit = ui_app.bid_repository.list_audit(None)[-1]
    waiting_after = cast(dict[str, object], json.loads(waiting_audit.detail))["after"]
    assert isinstance(waiting_after, dict)
    assert waiting_after["waiting_party_label"] == "Customer procurement"
    assert waiting_after["waiting_owed"] == "Signed clarification response"
    assert waiting_after["chase_date"] == "2026-08-20"

    reload_status, _, reload_html = asyncio.run(
        _asgi_request(ui_app.app, "GET", save_headers["location"])
    )
    assert reload_status == 200
    assert 'value="Customer procurement"' in reload_html
    assert "Signed clarification response" in reload_html
    assert 'value="2026-08-20"' in reload_html
    assert 'data-status-section="WAITING"><legend>Waiting details</legend>' in reload_html

    blocked_status, blocked_headers, _ = asyncio.run(
        _asgi_request(
            ui_app.app,
            "POST",
            f"/my-work/{created.work_item_id}",
            {
                "expected_version": str(stored.version),
                "title": stored.title,
                "details": stored.details or "",
                "category": stored.category.value,
                "responsibility_domain": stored.responsibility_domain.value,
                "priority": stored.priority.value,
                "status": "BLOCKED",
                "bid_id": stored.bid_id or "",
                "due_date": stored.due_date.isoformat(),
                "next_action_date": stored.next_action_date.isoformat(),
                "contribution_candidate": "true",
                "blocker_description": "Supplier quote has not arrived",
                "resolution_owner": "Commercial team",
                "review_date": "2026-08-21",
            },
        )
    )
    assert blocked_status == 303
    blocked = ui_app.work_item_repository.get(created.work_item_id)
    assert blocked is not None
    assert blocked.status.value == "BLOCKED"
    assert blocked.blocker_description == "Supplier quote has not arrived"
    assert blocked.blocker_note == "Supplier quote has not arrived"
    assert blocked.resolution_owner == "Commercial team"
    assert blocked.review_date == date(2026, 8, 21)
    assert blocked.waiting_party_label is None
    assert len(ui_app.bid_repository.list_audit(None)) == audit_before + 2
    blocked_reload = asyncio.run(_asgi_request(ui_app.app, "GET", blocked_headers["location"]))[2]
    assert "Supplier quote has not arrived" in blocked_reload
    assert 'value="Commercial team"' in blocked_reload

    completed_status, completed_headers, _ = asyncio.run(
        _asgi_request(
            ui_app.app,
            "POST",
            f"/my-work/{created.work_item_id}",
            {
                "expected_version": str(blocked.version),
                "title": blocked.title,
                "details": blocked.details or "",
                "category": blocked.category.value,
                "responsibility_domain": blocked.responsibility_domain.value,
                "priority": blocked.priority.value,
                "status": "COMPLETED",
                "bid_id": blocked.bid_id or "",
                "due_date": blocked.due_date.isoformat(),
                "next_action_date": blocked.next_action_date.isoformat(),
                "contribution_candidate": "true",
                "completion_outcome": "Clarification completed",
                "completion_evidence": "Accepted response record",
            },
        )
    )
    assert completed_status == 303
    completed = ui_app.work_item_repository.get(created.work_item_id)
    assert completed is not None
    assert completed.status.value == "COMPLETED"
    assert completed.completion_outcome == "Clarification completed"
    assert completed.completion_evidence == "Accepted response record"
    assert completed.completed_at is not None
    assert completed.blocker_description is None
    assert len(ui_app.bid_repository.list_audit(None)) == audit_before + 3
    completed_reload = asyncio.run(_asgi_request(ui_app.app, "GET", completed_headers["location"]))[
        2
    ]
    assert "Clarification completed" in completed_reload
    assert "Accepted response record" in completed_reload

    cancelled_status, cancelled_headers, _ = asyncio.run(
        _asgi_request(
            ui_app.app,
            "POST",
            f"/my-work/{created.work_item_id}",
            {
                "expected_version": str(completed.version),
                "title": completed.title,
                "details": completed.details or "",
                "category": completed.category.value,
                "responsibility_domain": completed.responsibility_domain.value,
                "priority": completed.priority.value,
                "status": "CANCELLED",
                "bid_id": completed.bid_id or "",
                "due_date": completed.due_date.isoformat(),
                "next_action_date": completed.next_action_date.isoformat(),
                "contribution_candidate": "true",
                "cancellation_reason": "No longer required",
            },
        )
    )
    assert cancelled_status == 303
    cancelled = ui_app.work_item_repository.get(created.work_item_id)
    assert cancelled is not None
    assert cancelled.status.value == "CANCELLED"
    assert cancelled.cancellation_reason == "No longer required"
    assert cancelled.completion_outcome == "Clarification completed"
    assert cancelled.completed_at is None
    assert len(ui_app.bid_repository.list_audit(None)) == audit_before + 4
    cancelled_reload = asyncio.run(_asgi_request(ui_app.app, "GET", cancelled_headers["location"]))[
        2
    ]
    assert "No longer required" in cancelled_reload

    unchanged_audit_count = len(ui_app.bid_repository.list_audit(None))
    stale_status, _, stale_html = asyncio.run(
        _asgi_request(
            ui_app.app,
            "POST",
            f"/my-work/{created.work_item_id}",
            {
                "expected_version": str(completed.version),
                "title": "Stale title must not persist",
                "category": cancelled.category.value,
                "priority": cancelled.priority.value,
                "status": "CANCELLED",
                "bid_id": cancelled.bid_id or "",
                "due_date": cancelled.due_date.isoformat(),
                "next_action_date": cancelled.next_action_date.isoformat(),
                "cancellation_reason": "Stale cancellation",
            },
        )
    )
    assert stale_status == 422
    assert "Stale work item version" in stale_html
    assert ui_app.work_item_repository.get(created.work_item_id) == cancelled
    assert len(ui_app.bid_repository.list_audit(None)) == unchanged_audit_count

    invalid_status, _, invalid_html = asyncio.run(
        _asgi_request(
            ui_app.app,
            "POST",
            f"/my-work/{created.work_item_id}",
            {
                "expected_version": str(cancelled.version),
                "title": "Invalid title must not persist",
                "category": cancelled.category.value,
                "priority": cancelled.priority.value,
                "status": "WAITING",
                "bid_id": cancelled.bid_id or "",
                "due_date": cancelled.due_date.isoformat(),
                "next_action_date": cancelled.next_action_date.isoformat(),
                "waiting_party_label": "",
                "waiting_owed": "",
                "chase_date": "",
            },
        )
    )
    assert invalid_status == 422
    assert "WAITING requires a waiting party" in invalid_html
    assert ui_app.work_item_repository.get(created.work_item_id) == cancelled
    assert len(ui_app.bid_repository.list_audit(None)) == unchanged_audit_count


def test_work_item_html_missing_record_returns_404(ui_app: ModuleType) -> None:
    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            ui_app.work_item_detail(
                cast(Request, object()), "WI-00000000-0000-0000-0000-000000000000"
            )
        )
    assert raised.value.status_code == 404


def test_command_center_filters_context_links_and_gets_are_read_only(
    ui_app: ModuleType,
    valid_bid: Bid,
) -> None:
    ui_app.bid_repository.create_bid(valid_bid)
    standalone = ui_app.work_item_service.create_work_item(
        {
            "title": "Standalone attention",
            "category": "CUSTOMER_REQUEST",
            "responsibility_domain": "CUSTOMER_SOLUTION",
            "next_action_date": "2026-08-05",
            "contribution_candidate": True,
        },
        "jason",
    )
    bid_work = ui_app.work_item_service.create_work_item(
        {
            "bid_id": valid_bid.bid_id,
            "title": "Bid history",
            "category": "COMMERCIAL_REVIEW",
        },
        "jason",
    )
    completed = ui_app.work_item_service.transition_work_item(
        bid_work.work_item_id,
        {
            "expected_version": bid_work.version,
            "status": "COMPLETED",
            "completion_outcome": "Reviewed",
        },
        "jason",
    )
    audit_before = len(ui_app.bid_repository.list_audit(None))

    current = _html(asyncio.run(ui_app.my_work(cast(Request, object()))))
    history = _html(asyncio.run(ui_app.my_work(cast(Request, object()), view="history")))
    all_work = _html(asyncio.run(ui_app.my_work(cast(Request, object()), view="all")))
    combined = _html(
        asyncio.run(
            ui_app.my_work(
                cast(Request, object()),
                category="CUSTOMER_REQUEST",
                domain="CUSTOMER_SOLUTION",
                context="standalone",
                attention="required",
            )
        )
    )
    bid_context = _html(
        asyncio.run(
            ui_app.my_work(
                cast(Request, object()),
                view="history",
                context="bid",
                bid_id=valid_bid.bid_id,
            )
        )
    )

    assert standalone.title in current and completed.title not in current
    assert completed.title in history and standalone.title not in history
    assert standalone.title in all_work and completed.title in all_work
    assert standalone.title in combined and completed.title not in combined
    assert "Standalone work" in combined
    assert f"{valid_bid.project_name} · {valid_bid.bid_id}" in bid_context
    assert f'href="/my-work/{standalone.work_item_id}"' in current
    assert ui_app.work_item_repository.get(standalone.work_item_id) == standalone
    assert ui_app.work_item_repository.get(standalone.work_item_id).contribution_candidate is True
    assert len(ui_app.bid_repository.list_audit(None)) == audit_before

    contradictory = asyncio.run(
        ui_app.my_work(
            cast(Request, object()),
            context="standalone",
            bid_id=valid_bid.bid_id,
        )
    )
    assert contradictory.status_code == 422
    assert "bid_id is only valid with context=bid" in _html(contradictory)


def test_my_day_work_rows_are_read_only_links_to_authoritative_editor(
    ui_app: ModuleType,
) -> None:
    item = ui_app.work_item_service.create_work_item(
        {"title": "Due command-center work", "due_date": "2026-08-05"},
        "jason",
    )

    page = _html(asyncio.run(ui_app.my_day(cast(Request, object()))))

    assert f'href="/my-work/{item.work_item_id}"' in page
    assert 'href="/my-work#quick-capture"' in page
    assert 'id="create-form"' not in page
    assert 'id="edit-form"' not in page
    assert 'id="status-form"' not in page
    assert "fetch(" not in page
