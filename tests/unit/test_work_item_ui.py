import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import cast
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from core.schemas import Bid


class JsonRequest:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def json(self) -> dict[str, object]:
        return dict(self.payload)


def _html(response: HTMLResponse) -> str:
    return bytes(response.body).decode()


def _json(response: JSONResponse) -> dict[str, object]:
    return cast(dict[str, object], json.loads(bytes(response.body)))


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
    return module


def test_empty_my_day_and_existing_navigation_render(ui_app: ModuleType) -> None:
    request = cast(Request, object())

    page = asyncio.run(ui_app.my_day(request, "2026-08-05"))
    dashboard = asyncio.run(ui_app.index(request))
    page_text = _html(page)
    dashboard_text = _html(dashboard)

    assert page.status_code == 200
    assert "Operational view as of" in page_text
    assert "No blocked work items." in page_text
    assert "No bids are currently on readiness hold." in page_text
    assert 'role="alert"' in page_text
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
    page = asyncio.run(ui_app.my_day(cast(Request, object()), "2026-08-05"))
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
                        "actor": "jason",
                    }
                ),
            ),
        )
    )
    assert completed_response.status_code == 200
    completed = _json(completed_response)
    completed_page = asyncio.run(ui_app.my_day(cast(Request, object()), "2026-08-05"))
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
    assert "WAITING requires waiting_on" in str(raised.value.detail)
    assert ui_app.work_item_repository.list() == []
    assert ui_app.bid_repository.list_audit(valid_bid.bid_id) == []
    page = asyncio.run(ui_app.my_day(cast(Request, object()), "2026-08-05"))
    assert "payload.detail" in _html(page)
    assert 'aria-live="assertive"' in _html(page)


def test_task06_hold_is_read_only_and_my_day_does_not_contact_alice(
    ui_app: ModuleType,
    valid_bid: Bid,
) -> None:
    ui_app.bid_repository.create_bid(valid_bid)

    with patch.object(
        ui_app.llm_client,
        "health_check",
        side_effect=AssertionError("My Day attempted an Alice/network call"),
    ) as health_check:
        response = asyncio.run(ui_app.my_day(cast(Request, object()), "2026-08-05"))

    assert response.status_code == 200
    assert "read-only from TASK-06" in _html(response)
    assert "Bid is on HOLD" in _html(response)
    assert valid_bid.bid_id in _html(response)
    health_check.assert_not_called()
