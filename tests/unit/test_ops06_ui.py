import asyncio
import importlib.util
import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from core.enums import BidLevel
from core.schemas import Bid


def _html(response: HTMLResponse) -> str:
    return bytes(response.body).decode()


def _section(page: str, marker: str) -> str:
    start = page.index(marker)
    return page[start : page.index("</section>", start) + len("</section>")]


@pytest.fixture
def ops06_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "ops06.db"))
    monkeypatch.setenv("CONTRACTIQ_DOCUMENT_ROOT", str(tmp_path / "documents"))
    sys.modules.pop("app", None)
    app_path = Path(__file__).parents[2] / "app.py"
    spec = importlib.util.spec_from_file_location("app", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load app.py for OPS-06 UI tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module._working_date = lambda: date(2026, 8, 5)
    return module


def test_home_navigation_administration_and_reports_are_role_aligned(
    ops06_app: ModuleType,
) -> None:
    request = cast(Request, object())
    home = _html(asyncio.run(ops06_app.index(request)))
    admin = _html(asyncio.run(ops06_app.administration()))
    reports = _html(asyncio.run(ops06_app.reports_center()))

    assert "<h1>My Day</h1>" in home
    for label, path in (
        ("My Day", "/my-day"),
        ("Bids", "/bids"),
        ("My Work", "/my-work"),
        ("Reports", "/reports-center"),
        ("Administration", "/administration"),
    ):
        assert f'href="{path}"' in home
        assert label in home
    assert 'href="/vendor-documents"' not in home
    assert 'href="/role-framework"' not in home
    assert "Role Framework" in admin and "Knowledge" in admin
    assert "Only currently supported exports" in reports


def test_all_six_sections_share_bid_header_rail_and_context(
    ops06_app: ModuleType,
    valid_bid: Bid,
) -> None:
    ops06_app.bid_repository.create_bid(valid_bid)
    before_bid = ops06_app.bid_repository.get_bid(valid_bid.bid_id)
    before_audit = ops06_app.bid_repository.list_audit(valid_bid.bid_id)
    pages = [
        _html(asyncio.run(ops06_app.bid_detail(cast(Request, object()), valid_bid.bid_id))),
        _html(asyncio.run(ops06_app.bid_requirements_scope(valid_bid.bid_id))),
        _html(asyncio.run(ops06_app.bid_manufacturers_coverage(valid_bid.bid_id))),
        _html(asyncio.run(ops06_app.bid_commercial_contract(valid_bid.bid_id))),
        _html(asyncio.run(ops06_app.bid_proposal_negotiation(valid_bid.bid_id))),
        _html(asyncio.run(ops06_app.bid_award_handover(valid_bid.bid_id))),
    ]

    for page in pages:
        assert valid_bid.bid_id in page
        assert valid_bid.customer in page
        assert valid_bid.project_name in page
        assert str(valid_bid.customer_due_date) in page
        assert str(valid_bid.internal_due_date) in page
        assert "Gate and readiness rail" in page
        assert "Next required action" in page
        assert page.count("Bid workspace sections") == 1
    assert len({_section(page, 'aria-label="Bid header"') for page in pages}) == 1
    assert len({_section(page, 'aria-label="Gate and readiness rail"') for page in pages}) == 1
    assert "Vendor Document Requirements" in pages[2]
    assert "post-award execution" in pages[5]
    assert ops06_app.bid_repository.get_bid(valid_bid.bid_id) == before_bid
    assert ops06_app.bid_repository.list_audit(valid_bid.bid_id) == before_audit


def test_classification_changes_control_presentation(
    ops06_app: ModuleType,
    valid_bid: Bid,
) -> None:
    level_one = valid_bid.model_copy(update={"classification": BidLevel.LEVEL_1})
    level_three = valid_bid.model_copy(
        update={"bid_id": "B-2026-0003", "classification": BidLevel.LEVEL_3}
    )
    ops06_app.bid_repository.create_bid(level_one)
    ops06_app.bid_repository.create_bid(level_three)

    one = _html(asyncio.run(ops06_app.bid_detail(cast(Request, object()), level_one.bid_id)))
    three = _html(asyncio.run(ops06_app.bid_detail(cast(Request, object()), level_three.bid_id)))

    assert "Not required for Level 0 or Level 1" in one
    assert "Required for Level 2, Level 3 and Level 4" in three


def test_portfolio_filters_render_and_invalid_filter_is_422(
    ops06_app: ModuleType,
    valid_bid: Bid,
) -> None:
    ops06_app.bid_repository.create_bid(valid_bid)
    page = _html(
        asyncio.run(
            ops06_app.bids_projects(
                view="current",
                classification=valid_bid.classification.value,
                owner="Coordinator",
                deadline="overdue",
            )
        )
    )
    assert valid_bid.bid_id in page
    assert "Highest blocker" in page
    assert "Next required action" in page

    before_bid = ops06_app.bid_repository.get_bid(valid_bid.bid_id)
    before_audit = ops06_app.bid_repository.list_audit()
    invalid = asyncio.run(
        ops06_app.bids_projects(
            view="nonsense",
            status="active",
            owner="Jason",
        )
    )
    invalid_page = _html(invalid)
    assert invalid.status_code == 422
    assert invalid.media_type == "text/html"
    assert "Could not apply portfolio filters" in invalid_page
    assert '<option value="nonsense" selected>Invalid selection: nonsense</option>' in invalid_page
    assert '<option value="active" selected>' in invalid_page
    assert 'name="owner" value="Jason"' in invalid_page
    assert '{"detail"' not in invalid_page

    invalid_readiness = asyncio.run(ops06_app.bids_projects(readiness="escalate"))
    assert invalid_readiness.status_code == 422
    assert "Invalid selection: escalate" in _html(invalid_readiness)

    contradiction = asyncio.run(
        ops06_app.bids_projects(view="history", status="active", owner="Jason")
    )
    contradiction_page = _html(contradiction)
    assert contradiction.status_code == 422
    assert "History view cannot be combined" in contradiction_page
    assert 'name="owner" value="Jason"' in contradiction_page
    assert ops06_app.bid_repository.get_bid(valid_bid.bid_id) == before_bid
    assert ops06_app.bid_repository.list_audit() == before_audit


def test_operational_registers_return_directly_to_the_active_bid(
    ops06_app: ModuleType,
    valid_bid: Bid,
) -> None:
    ops06_app.bid_repository.create_bid(valid_bid)
    request = cast(Request, object())
    routes = (
        (
            lambda: ops06_app.requirements_register(request, bid_id=valid_bid.bid_id),
            "requirements-scope",
            "Requirements &amp; Scope",
        ),
        (
            lambda: ops06_app.controlled_documents(request, bid_id=valid_bid.bid_id),
            "requirements-scope",
            "Requirements &amp; Scope",
        ),
        (
            lambda: ops06_app.scope_interfaces_register(valid_bid.bid_id),
            "requirements-scope",
            "Requirements &amp; Scope",
        ),
        (
            lambda: ops06_app.suppliers_register(valid_bid.bid_id),
            "manufacturers-coverage",
            "Manufacturers &amp; Coverage",
        ),
        (
            lambda: ops06_app.commercial_register(valid_bid.bid_id),
            "commercial-contract",
            "Commercial &amp; Contract",
        ),
        (
            lambda: ops06_app.contract_risks_register(valid_bid.bid_id),
            "commercial-contract",
            "Commercial &amp; Contract",
        ),
        (
            lambda: ops06_app.decisions_register(valid_bid.bid_id),
            "commercial-contract",
            "Commercial &amp; Contract",
        ),
        (
            lambda: ops06_app.deliverables_register(valid_bid.bid_id),
            "proposal-negotiation",
            "Proposal &amp; Negotiation",
        ),
        (
            lambda: ops06_app.proposals_register(valid_bid.bid_id),
            "proposal-negotiation",
            "Proposal &amp; Negotiation",
        ),
        (
            lambda: ops06_app.negotiations_register(valid_bid.bid_id),
            "proposal-negotiation",
            "Proposal &amp; Negotiation",
        ),
    )
    for route, section, label in routes:
        page = _html(asyncio.run(route()))
        assert f'href="/bids/{valid_bid.bid_id}/{section}"' in page
        assert f"Back to Bid {valid_bid.bid_id} — {label}" in page

    without_bid = _html(asyncio.run(ops06_app.proposals_register()))
    assert "Back to Bid" not in without_bid


def test_single_bid_workspace_query_count_is_portfolio_size_independent(
    ops06_app: ModuleType,
    valid_bid: Bid,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ops06_app.bid_repository.create_bid(valid_bid)
    original_connection = ops06_app.db._conn

    def measured_workspace() -> int:
        statements: list[str] = []

        def traced_connection() -> sqlite3.Connection:
            connection = original_connection()
            connection.set_trace_callback(statements.append)
            return connection

        with monkeypatch.context() as context:
            context.setattr(ops06_app.db, "_conn", traced_connection)
            workspace = ops06_app.bid_control_center_service.workspace(
                valid_bid.bid_id,
                as_of=date(2026, 8, 5),
            )
        assert workspace.bid.bid_id == valid_bid.bid_id
        return len(statements)

    counts = {1: measured_workspace()}
    for number in range(2, 37):
        ops06_app.bid_repository.create_bid(
            valid_bid.model_copy(
                update={
                    "bid_id": f"B-2026-{number:04d}",
                    "project_name": f"Unrelated Bid {number:02d}",
                }
            )
        )
        if number in {12, 36}:
            counts[number] = measured_workspace()

    assert max(counts.values()) - min(counts.values()) <= 2, counts


def test_missing_bid_is_404_and_normal_pages_hide_internal_milestones(
    ops06_app: ModuleType,
) -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ops06_app.bid_requirements_scope("B-MISSING"))
    assert exc.value.status_code == 404

    normal_pages = [
        _html(asyncio.run(ops06_app.index(cast(Request, object())))),
        _html(asyncio.run(ops06_app.bids_projects())),
    ]
    for page in normal_pages:
        assert "TASK-" not in page
        assert "OPS-" not in page
        assert "raw JSON" not in page
