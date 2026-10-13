"""Socketless user-journey coverage for Stage 3 execution forms."""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from urllib.parse import quote, urlencode

import pytest

from core.bid_execution import ExecutionSubjectKind, PrepStatus
from core.bid_execution_projection import ExecutionFilter, ExecutionListKind
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


def _post_pairs(
    module: ModuleType, path: str, form: list[tuple[str, str]]
) -> tuple[int, dict[str, str], str]:
    status, headers, body = asyncio.run(
        request(
            module.app,
            "POST",
            path,
            body=urlencode(form).encode(),
            content_type="application/x-www-form-urlencoded",
        )
    )
    return status, headers, body.decode()


def _reload_app(module: ModuleType) -> ModuleType:
    app_path = Path(module.__file__ or "")
    sys.modules.pop("app", None)
    spec = importlib.util.spec_from_file_location("app", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to reload app.py for Stage 3 persistence tests")
    reloaded = importlib.util.module_from_spec(spec)
    sys.modules["app"] = reloaded
    spec.loader.exec_module(reloaded)
    reloaded._working_date = lambda: date(2026, 7, 10)
    return reloaded


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


def test_source_panel_suppresses_unlocated_duplicate_source(
    forms_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = forms_app
    with module.db._conn() as conn:
        conn.execute(
            """INSERT INTO deliverable_discovery_destinations(
            destination_id,proposal_id,event_id,bid_id,destination_kind,record_id,reused,note,
            created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                "destination-stage3-ui-source-test",
                ids["finding"],
                "review-stage3-ui",
                ids["bid"],
                "DELIVERABLE",
                ids["returnable"],
                0,
                None,
                "2026-07-10T14:30:00+00:00",
            ),
        )
        conn.execute(
            """INSERT INTO deliverable_links(
            link_id,bid_id,deliverable_id,target_type,target_id,relation,created_at,created_by
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                "DLK-STAGE3-UI-BLANK-SOURCE",
                ids["bid"],
                ids["returnable"],
                "RECEIVED_FILE",
                "FILE-NOT-PRESENT",
                "SOURCE_RECEIVED_FILE",
                "2026-07-10T14:30:00+00:00",
                "Jason",
            ),
        )

    trace = module.bid_execution_trace.source_trace(ids["bid"], "RETURNABLE", ids["returnable"])
    assert len(trace.sources) == 1
    assert trace.sources[0].locator == "page 7"
    assert trace.sources[0].verified_quote == ids["quote"]

    status, page = _get(
        module,
        f"/bids/{ids['bid']}/execution/items/returnable/{ids['returnable']}",
    )
    assert status == 200
    source_panel = re.search(
        r'<section class="card execution-source-panel">(.*?)</section>', page, re.DOTALL
    )
    assert source_panel is not None
    assert source_panel.group(1).count("<article>") == 1
    assert "Locator</dt><dd>Not recorded" not in source_panel.group(1)
    assert "No verified quote recorded" not in source_panel.group(1)


def test_blank_priority_stays_not_set_and_does_not_replace_action_priority(
    forms_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = forms_app
    bid_id = ids["bid"]
    returnable_base = f"/bids/{bid_id}/execution/items/returnable/{ids['returnable']}"
    status, page = _get(module, returnable_base)
    assert status == 200
    select = re.search(r'<select name="priority"([^>]*)>(.*?)</select>', page, re.DOTALL)
    assert select is not None
    assert "required" not in select.group(1)
    assert '<option value="" selected>Not set</option>' in select.group(2)

    status, _headers, _body = _post(
        module,
        f"{returnable_base}/plan",
        {
            "expected_version": "0",
            "responsible": "",
            "contributors": "",
            "internal_due_date": "",
            "priority": "",
            "return_to": returnable_base,
        },
    )
    assert status == 303
    assert module.bid_execution_service.get_record(bid_id, "RETURNABLE", ids["returnable"]) is None
    assert "<strong>Not set</strong>" in _get(module, returnable_base)[1]

    status, _headers, invalid = _post(
        module,
        f"{returnable_base}/plan",
        {
            "expected_version": "0",
            "responsible": "",
            "contributors": "",
            "internal_due_date": "",
            "priority": "Urgent",
            "return_to": returnable_base,
        },
    )
    assert status == 422
    assert "priority is invalid; choose one of the listed priorities" in invalid

    work = module.work_item_service.get_work_item(ids["work"])
    action_base = f"/bids/{bid_id}/execution/items/work-item/{ids['work']}"
    status, _headers, _body = _post(
        module,
        f"{action_base}/plan",
        {
            "expected_version": str(work.version),
            "overlay_expected_version": "0",
            "responsible": "",
            "contributors": "",
            "internal_due_date": work.due_date.isoformat() if work.due_date else "",
            "priority": "",
            "return_to": action_base,
        },
    )
    assert status == 303
    assert module.work_item_service.get_work_item(ids["work"]).priority.value == "HIGH"


def test_prerequisite_selects_render_kind_groups(
    forms_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = forms_app
    paths = (
        f"/bids/{ids['bid']}/execution/items/returnable/{ids['returnable']}",
        f"/bids/{ids['bid']}/execution/actions?state=all",
    )
    for path in paths:
        status, page = _get(module, path)
        assert status == 200
        for label in ("Returnables", "Manufacturer inputs", "Actions", "Commercial"):
            assert f'<optgroup label="{label}">' in page


def test_bulk_assign_http_validates_every_selection_before_writing(
    forms_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = forms_app
    path = f"/bids/{ids['bid']}/execution/returnables/assign"
    before = _table_counts(module)
    status, _headers, body = _post_pairs(
        module,
        path,
        [
            ("subject_ids", ids["returnable"]),
            ("subject_ids", "DEL-NOT-IN-THIS-BID"),
            ("responsible", "Alex"),
            ("internal_due_date", "2026-07-20"),
            ("return_to", f"/bids/{ids['bid']}/execution/returnables"),
        ],
    )
    assert status == 422
    assert "selected item" in body or "existing returnable" in body
    assert _table_counts(module) == before
    assert (
        module.bid_execution_service.get_record(ids["bid"], "RETURNABLE", ids["returnable"]) is None
    )

    status, _headers, body = _post(
        module,
        path,
        {
            "responsible": "Alex",
            "internal_due_date": "2026-07-20",
            "return_to": f"/bids/{ids['bid']}/execution/returnables",
        },
    )
    assert status == 422
    assert "select at least one outstanding item" in body
    assert _table_counts(module) == before


def test_writes_survive_app_reload_without_duplicate_overlay_or_dependency(
    forms_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = forms_app
    bid_id = ids["bid"]
    base = f"/bids/{bid_id}/execution/items/returnable/{ids['returnable']}"
    plan = {
        "expected_version": "0",
        "responsible": "Alex",
        "contributors": "Sam",
        "internal_due_date": "2026-07-20",
        "priority": "",
        "return_to": base,
    }
    assert _post(module, f"{base}/plan", plan)[0] == 303
    assert (
        _post(
            module,
            f"{base}/notes",
            {"body": "Waiting for the final schedule.", "return_to": base},
        )[0]
        == 303
    )
    assert (
        _post(
            module,
            f"{base}/prerequisites",
            {
                "prerequisite": f"WORK_ITEM|{ids['work']}",
                "note": "Legal review first",
                "return_to": base,
            },
        )[0]
        == 303
    )

    original = module.bid_execution_service.get_record(bid_id, "RETURNABLE", ids["returnable"])
    assert original is not None
    plan["expected_version"] = str(original.version)
    assert _post(module, f"{base}/plan", plan)[0] == 303

    reloaded = _reload_app(module)
    persisted = reloaded.bid_execution_service.get_record(bid_id, "RETURNABLE", ids["returnable"])
    assert persisted == original
    notes = reloaded.bid_execution_service.list_notes(bid_id, "RETURNABLE", ids["returnable"])
    assert [note.body for note in notes] == ["Waiting for the final schedule."]
    active = reloaded.bid_execution_repository.list_active_dependencies_for_subject(
        bid_id, ExecutionSubjectKind.RETURNABLE, ids["returnable"]
    )
    assert len(active) == 1
    assert active[0].prerequisite_id == ids["work"]

    plan["expected_version"] = str(persisted.version)
    assert _post(reloaded, f"{base}/plan", plan)[0] == 303
    duplicate_status, _headers, duplicate_page = _post(
        reloaded,
        f"{base}/prerequisites",
        {
            "prerequisite": f"WORK_ITEM|{ids['work']}",
            "note": "Legal review first",
            "return_to": base,
        },
    )
    assert duplicate_status == 422
    assert "already" in duplicate_page
    with reloaded.db._conn() as conn:
        overlay_count = conn.execute(
            """SELECT COUNT(*) FROM bid_execution_records
            WHERE bid_id=? AND subject_kind='RETURNABLE' AND subject_id=?""",
            (bid_id, ids["returnable"]),
        ).fetchone()[0]
        dependency_count = conn.execute(
            """SELECT COUNT(*) FROM bid_execution_dependencies
            WHERE bid_id=? AND item_kind='RETURNABLE' AND item_id=? AND removed_at IS NULL""",
            (bid_id, ids["returnable"]),
        ).fetchone()[0]
    assert overlay_count == 1
    assert dependency_count == 1


def test_execution_route_table_has_only_advisory_workflow_mutations(
    forms_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, _ids = forms_app
    forbidden = ("approve", "submit", "authorize")
    allowed_posts = (
        "plan",
        "progress",
        "notes",
        "prerequisites",
        "actions",
        "assign",
        "confirm-source-owner",
        "confirm-source-owners",
    )
    for route in module.app.routes:
        path = str(getattr(route, "path", "")).casefold()
        if "/execution" not in path:
            continue
        assert not any(word in path for word in forbidden)
        if "POST" in (getattr(route, "methods", set()) or set()):
            assert any(operation in path for operation in allowed_posts)


def test_completing_prerequisite_does_not_mutate_dependent_item(
    forms_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = forms_app
    bid_id = ids["bid"]
    dependent_base = f"/bids/{bid_id}/execution/items/returnable/{ids['returnable']}"
    assert (
        _post(
            module,
            f"{dependent_base}/progress",
            {
                "expected_version": "0",
                "status": "In progress",
                "return_to": dependent_base,
            },
        )[0]
        == 303
    )
    assert (
        _post(
            module,
            f"{dependent_base}/prerequisites",
            {
                "prerequisite": f"WORK_ITEM|{ids['work']}",
                "return_to": dependent_base,
            },
        )[0]
        == 303
    )
    before_record = module.bid_execution_service.get_record(bid_id, "RETURNABLE", ids["returnable"])
    before_item = next(
        item
        for item in module.execution_projection_service.items(
            bid_id, ExecutionListKind.RETURNABLE, ExecutionFilter.ALL, date(2026, 7, 10)
        )
        if item.subject_id == ids["returnable"]
    )
    assert before_item.status is PrepStatus.IN_PROGRESS
    assert before_item.waiting_on_prerequisite

    work = module.work_item_service.get_work_item(ids["work"])
    work_base = f"/bids/{bid_id}/execution/items/work-item/{ids['work']}"
    status, _headers, _body = _post(
        module,
        f"{work_base}/progress",
        {
            "expected_version": str(work.version),
            "status": "Complete",
            "completion_outcome": "Legal review completed",
            "completion_evidence": "Review note dated 2026-07-10",
            "return_to": work_base,
        },
    )
    assert status == 303
    after_record = module.bid_execution_service.get_record(bid_id, "RETURNABLE", ids["returnable"])
    after_item = next(
        item
        for item in module.execution_projection_service.items(
            bid_id, ExecutionListKind.RETURNABLE, ExecutionFilter.ALL, date(2026, 7, 10)
        )
        if item.subject_id == ids["returnable"]
    )
    assert after_record == before_record
    assert after_item.status is PrepStatus.IN_PROGRESS
    assert not after_item.waiting_on_prerequisite
