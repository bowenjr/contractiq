"""Socketless user-journey coverage for Stage 3 execution forms."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import ModuleType
from urllib.parse import quote

import pytest

from core.schemas import Bid
from scripts.asgi_acceptance_ops08 import request
from tests.unit.test_stage3_ui import ui_app as seeded_ui_app


@pytest.fixture
def forms_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid_bid: Bid
) -> tuple[ModuleType, dict[str, str]]:
    """Reuse the representative seeded Bid from the read-only route tests."""
    return seeded_ui_app.__wrapped__(tmp_path, monkeypatch, valid_bid)


def _post(module: ModuleType, path: str, form: dict[str, str]) -> tuple[int, dict[str, str], str]:
    status, headers, body = asyncio.run(request(module.app, "POST", path, form))
    return status, headers, body.decode()


def _get(module: ModuleType, path: str) -> tuple[int, str]:
    status, _headers, body = asyncio.run(request(module.app, "GET", path))
    return status, body.decode()


def _table_counts(module: ModuleType) -> tuple[int, int, int, int]:
    with module.db._conn() as conn:
        return tuple(
            int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "work_items",
                "bid_execution_records",
                "bid_execution_notes",
                "audit_log",
            )
        )  # type: ignore[return-value]


def test_returnable_plan_and_progress_journey(
    forms_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = forms_app
    bid_id = ids["bid"]
    subject_id = ids["returnable"]
    base = f"/bids/{bid_id}/execution/items/returnable/{subject_id}"

    status, headers, _body = _post(
        module,
        f"{base}/plan",
        {
            "expected_version": "0",
            "responsible": "Alex",
            "contributors": "Sam, Pat",
            "internal_due_date": "2026-07-20",
            "priority": "High",
            "return_to": base,
        },
    )
    assert status == 303 and "notice=" in headers["location"]
    record = module.bid_execution_service.get_record(bid_id, "RETURNABLE", subject_id)
    assert record is not None
    assert record.responsible == "Alex" and record.contributors == ["Sam", "Pat"]
    assert record.internal_due_date.isoformat() == "2026-07-20"

    status, _headers, _body = _post(
        module,
        f"{base}/progress",
        {
            "expected_version": str(record.version),
            "status": "In progress",
            "return_to": base,
        },
    )
    assert status == 303
    record = module.bid_execution_service.get_record(bid_id, "RETURNABLE", subject_id)
    assert record is not None

    status, _headers, body = _post(
        module,
        f"{base}/progress",
        {
            "expected_version": str(record.version),
            "status": "Blocked",
            "blocked_reason": "",
            "return_to": base,
        },
    )
    assert status == 422
    assert "describe what is blocking preparation" in body

    status, _headers, _body = _post(
        module,
        f"{base}/progress",
        {
            "expected_version": str(record.version),
            "status": "Blocked",
            "blocked_reason": "Pricing is missing",
            "return_to": base,
        },
    )
    assert status == 303
    record = module.bid_execution_service.get_record(bid_id, "RETURNABLE", subject_id)
    assert record is not None
    status, _headers, _body = _post(
        module,
        f"{base}/progress",
        {
            "expected_version": str(record.version),
            "status": "Complete",
            "completion_evidence": "Final response pack, page 2",
            "return_to": base,
        },
    )
    assert status == 303
    dashboard = _get(module, f"/bids/{bid_id}/execution")[1]
    readiness = _get(module, f"/bids/{bid_id}/execution/readiness")[1]
    assert 'data-tile="returnables_complete" data-count="1"' in dashboard
    assert "Mandatory returnables are incomplete" not in readiness


def test_manual_action_repeat_is_idempotent(
    forms_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = forms_app
    path = f"/bids/{ids['bid']}/execution/actions"
    form = {
        "title": "Coordinate final review",
        "details": "Bring the final response together",
        "responsible": "",
        "contributors": "",
        "due_date": "",
        "priority": "Normal",
        "prerequisite": "",
        "return_to": path,
    }
    status, _headers, _body = _post(module, path, form)
    assert status == 303
    before_repeat = _table_counts(module)
    status, headers, _body = _post(module, path, form)
    assert status == 303
    assert "already+exists" in headers["location"]
    assert _table_counts(module) == before_repeat


def test_refusals_escape_notices_and_manufacturer_status_is_read_only(
    forms_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = forms_app
    bid_id = ids["bid"]
    status, body = _get(
        module,
        f"/bids/{bid_id}/execution?notice={quote('<script>alert(1)</script>')}",
    )
    assert status == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>alert(1)</script>" not in body

    status, manufacturer = _get(
        module,
        f"/bids/{bid_id}/execution/items/manufacturer-input/{ids['manufacturer']}",
    )
    assert status == 200
    assert 'name="status"' not in manufacturer
    assert "Status is controlled by manufacturer-requirement verification" in manufacturer

    status, _headers, body = _post(
        module,
        f"/bids/{bid_id}/execution/returnables/assign",
        {"responsible": "Alex", "internal_due_date": "", "return_to": ""},
    )
    assert status == 422
    assert "select at least one outstanding item" in body

    forbidden = ("approve", "submit", "authorize")
    execution_paths = [
        route.path.casefold() for route in module.app.routes if "/execution" in route.path
    ]
    assert not any(word in path for path in execution_paths for word in forbidden)


def test_due_date_and_dependency_loop_refusals_preserve_the_page(
    forms_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = forms_app
    bid_id = ids["bid"]
    returnable = ids["returnable"]
    work = ids["work"]
    returnable_base = f"/bids/{bid_id}/execution/items/returnable/{returnable}"
    work_base = f"/bids/{bid_id}/execution/items/work-item/{work}"

    status, _headers, body = _post(
        module,
        f"{returnable_base}/plan",
        {
            "expected_version": "0",
            "responsible": "Alex",
            "contributors": "",
            "internal_due_date": "2026-08-01",
            "priority": "Normal",
            "return_to": returnable_base,
        },
    )
    assert status == 422
    assert "choose a date on or before the customer deadline" in body
    assert 'value="Alex"' in body

    status, _headers, _body = _post(
        module,
        f"{work_base}/prerequisites",
        {
            "prerequisite": f"RETURNABLE|{returnable}",
            "return_to": work_base,
        },
    )
    assert status == 303
    status, _headers, body = _post(
        module,
        f"{returnable_base}/prerequisites",
        {"prerequisite": f"WORK_ITEM|{work}", "return_to": returnable_base},
    )
    assert status == 422
    assert "which already depends on" in body
    assert "remove an existing dependency" in body
