import asyncio
import importlib.util
import sys
from datetime import date
from types import ModuleType
from typing import cast

import pytest
from fastapi import Request
from fastapi.responses import HTMLResponse


def _text(response: HTMLResponse) -> str:
    return bytes(response.body).decode()


@pytest.fixture
def ui_app(tmp_path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "navigation.db"))
    sys.modules.pop("app", None)
    app_path = __file__.split("tests/unit/test_ops_navigation.py")[0] + "app.py"
    spec = importlib.util.spec_from_file_location("app", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module._working_date = lambda: date(2026, 8, 5)
    return module


def test_home_exposes_operational_workspace_navigation(ui_app: ModuleType) -> None:
    page = _text(asyncio.run(ui_app.index(cast(Request, object()))))
    assert 'href="/my-day"' in page
    assert 'href="/bids"' in page
    assert 'href="/my-work"' in page
    assert 'href="/reports-center"' in page
    assert 'href="/administration"' in page
    assert 'href="/role-framework"' not in page
    assert 'href="/vendor-documents"' not in page
    assert "My Day" in page


def test_operational_pages_share_navigation_and_quick_capture(ui_app: ModuleType) -> None:
    request = cast(Request, object())
    pages = [
        _text(asyncio.run(ui_app.my_day(request))),
        _text(asyncio.run(ui_app.my_work(request))),
        _text(asyncio.run(ui_app.bids_projects())),
        _text(asyncio.run(ui_app.administration())),
        _text(asyncio.run(ui_app.reports_center())),
    ]
    for page in pages:
        assert 'href="/my-day"' in page
        assert 'href="/my-work"' in page
        assert 'href="/bids"' in page
        assert 'href="/reports-center"' in page
        assert 'href="/administration"' in page
    my_work = pages[1]
    assert "Quick Capture" in my_work
    assert 'id="quick-capture"' in my_work
    assert "Use Quick Capture from My Day." not in my_work
