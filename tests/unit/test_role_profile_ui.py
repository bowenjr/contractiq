import asyncio
import importlib.util
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import cast
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import pytest


async def _request(
    application: object,
    method: str,
    path: str,
    *,
    form: dict[str, str | list[str]] | None = None,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, str], str]:
    sent: list[dict[str, object]] = []
    if payload is not None:
        body = json.dumps(payload).encode()
        headers = [(b"content-type", b"application/json")]
    else:
        body = urlencode(form or {}, doseq=True).encode()
        headers = (
            [(b"content-type", b"application/x-www-form-urlencoded")]
            if method in {"POST", "PATCH"}
            else []
        )
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict[str, object]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
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
def role_ui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "roles-ui.db"))
    monkeypatch.setenv("CONTRACTIQ_DOCUMENT_ROOT", str(tmp_path / "documents"))
    sys.modules.pop("app", None)
    app_path = Path(__file__).parents[2] / "app.py"
    spec = importlib.util.spec_from_file_location("app", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load app.py for role-profile UI tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module._working_date = lambda: date(2026, 9, 1)
    module.role_profile_service._now_factory = lambda: datetime(2026, 8, 17, 14, tzinfo=UTC)
    ids = iter(UUID(int=value) for value in range(1, 500))
    module.role_profile_service._id_factory = lambda: next(ids)
    return module


def _complete_form(title: str, start: str = "2026-09-01") -> dict[str, str | list[str]]:
    return {
        "title": title,
        "organization": "Example organization",
        "mission": "Deliver controlled bids and contracts outcomes.",
        "boundaries": "No silent commercial authority.",
        "coordination": "Coordinate sales, suppliers, and governance.",
        "outcomes": "Auditable decisions and complete offers.",
        "cadence": "Daily attention and weekly portfolio review.",
        "domains": ["RISK_ASSURANCE", "CUSTOMER_SOLUTION", "RISK_ASSURANCE"],
        "effective_from": start,
        "effective_until": "",
    }


async def _empty_state_create_edit_reload_and_stale_html(
    role_ui_app: ModuleType,
) -> None:
    status, _, landing = await _request(role_ui_app.app, "GET", "/role-framework")
    assert status == 200
    assert "Role framework setup required" in landing
    assert 'href="/role-framework/new"' in landing
    status, _, new_page = await _request(role_ui_app.app, "GET", "/role-framework/new")
    assert status == 200
    assert 'name="title"' in new_page and 'name="domains"' in new_page

    status, headers, _ = await _request(
        role_ui_app.app,
        "POST",
        "/role-framework",
        form={"title": "Browser draft", "effective_from": "2026-09-01"},
    )
    assert status == 303
    location = headers["location"]
    profile_id = location.rsplit("/", 1)[-1]
    created = role_ui_app.role_profile_service.get_profile(profile_id)
    assert created.title == "Browser draft" and created.mission == ""
    assert len(role_ui_app.bid_repository.list_audit(None)) == 1

    status, _, created_html = await _request(role_ui_app.app, "GET", location)
    assert status == 200
    assert 'value="Browser draft"' in created_html
    assert created.version_token in created_html

    edit_form = {
        **_complete_form("Browser role edited"),
        "expected_version_token": created.version_token,
    }
    status, headers, _ = await _request(
        role_ui_app.app,
        "POST",
        location,
        form=edit_form,
    )
    assert status == 303 and headers["location"] == location
    edited = role_ui_app.role_profile_service.get_profile(profile_id)
    assert edited.title == "Browser role edited"
    assert [domain.value for domain in edited.domains] == [
        "CUSTOMER_SOLUTION",
        "RISK_ASSURANCE",
    ]
    status, _, edited_html = await _request(role_ui_app.app, "GET", location)
    assert status == 200
    assert "Deliver controlled bids" in edited_html
    assert "Customer Solution" in edited_html

    audit_before = role_ui_app.bid_repository.list_audit(None)
    stale_form = {
        **_complete_form("Retained stale title"),
        "expected_version_token": created.version_token,
    }
    status, _, stale_html = await _request(
        role_ui_app.app,
        "POST",
        location,
        form=stale_form,
    )
    assert status == 422
    assert "Stale role profile token" in stale_html
    assert 'value="Retained stale title"' in stale_html
    assert role_ui_app.role_profile_service.get_profile(profile_id) == edited
    assert role_ui_app.bid_repository.list_audit(None) == audit_before


def test_empty_state_create_edit_reload_and_stale_html(role_ui_app: ModuleType) -> None:
    asyncio.run(_empty_state_create_edit_reload_and_stale_html(role_ui_app))


async def _html_publish_revision_atomic_supersession_and_retirement(
    role_ui_app: ModuleType,
) -> None:
    status, headers, _ = await _request(
        role_ui_app.app,
        "POST",
        "/role-framework",
        form=_complete_form("Published parent"),
    )
    assert status == 303
    parent_path = headers["location"]
    parent_id = parent_path.rsplit("/", 1)[-1]
    parent = role_ui_app.role_profile_service.get_profile(parent_id)
    status, headers, _ = await _request(
        role_ui_app.app,
        "POST",
        f"{parent_path}/publish",
        form={"expected_version_token": parent.version_token},
    )
    assert status == 303 and headers["location"] == parent_path
    parent = role_ui_app.role_profile_service.get_profile(parent_id)

    status, headers, _ = await _request(
        role_ui_app.app,
        "POST",
        f"{parent_path}/revise",
        form={"expected_version_token": parent.version_token},
    )
    assert status == 303
    child_path = headers["location"]
    child_id = child_path.rsplit("/", 1)[-1]
    child = role_ui_app.role_profile_service.get_profile(child_id)
    parent_after_revision = role_ui_app.role_profile_service.get_profile(parent_id)

    status, _, duplicate_html = await _request(
        role_ui_app.app,
        "POST",
        f"{parent_path}/revise",
        form={"expected_version_token": parent_after_revision.version_token},
    )
    assert status == 422 and "active DRAFT revision" in duplicate_html

    status, _, _ = await _request(
        role_ui_app.app,
        "POST",
        child_path,
        form={
            **_complete_form("Published child", "2026-10-01"),
            "expected_version_token": child.version_token,
        },
    )
    assert status == 303
    child = role_ui_app.role_profile_service.get_profile(child_id)
    status, headers, _ = await _request(
        role_ui_app.app,
        "POST",
        f"{child_path}/publish",
        form={
            "expected_version_token": child.version_token,
            "parent_expected_version_token": parent_after_revision.version_token,
        },
    )
    assert status == 303 and headers["location"] == child_path
    parent_closed = role_ui_app.role_profile_service.get_profile(parent_id)
    child_published = role_ui_app.role_profile_service.get_profile(child_id)
    assert parent_closed.effective_until == date(2026, 9, 30)
    assert child_published.state.value == "PUBLISHED"
    assert (
        role_ui_app.role_profile_service.effective_profile(as_of=date(2026, 9, 30)).profile_id
        == parent_id
    )
    assert (
        role_ui_app.role_profile_service.effective_profile(as_of=date(2026, 10, 1)).profile_id
        == child_id
    )

    status, _, child_html = await _request(role_ui_app.app, "GET", child_path)
    assert status == 200
    assert f'href="/role-framework/{parent_id}"' in child_html
    assert "Role Profile Superseded" in child_html
    assert "Human-authored by" in child_html
    assert 'name="title"' not in child_html

    status, _, _ = await _request(
        role_ui_app.app,
        "POST",
        f"{child_path}/retire",
        form={"expected_version_token": child_published.version_token},
    )
    assert status == 303
    assert role_ui_app.role_profile_service.get_profile(child_id).state.value == "RETIRED"


def test_html_publish_revision_atomic_supersession_and_retirement(
    role_ui_app: ModuleType,
) -> None:
    asyncio.run(_html_publish_revision_atomic_supersession_and_retirement(role_ui_app))


async def _gets_are_non_mutating_missing_is_404_and_delete_is_absent(
    role_ui_app: ModuleType,
) -> None:
    created = role_ui_app.role_profile_service.create_profile(
        {
            "title": "Read only",
            "effective_from": "2026-09-01",
        },
        "test",
    )
    profiles_before = role_ui_app.role_profile_service.list_profiles()
    audit_before = role_ui_app.bid_repository.list_audit(None)
    for path in (
        "/role-framework",
        f"/role-framework/{created.profile_id}",
        "/api/ops/role-profiles",
    ):
        status, _, _ = await _request(role_ui_app.app, "GET", path)
        assert status == 200
    status, _, _ = await _request(
        role_ui_app.app,
        "GET",
        "/role-framework/RPF-missing",
    )
    assert status == 404
    status, _, _ = await _request(
        role_ui_app.app,
        "DELETE",
        f"/role-framework/{created.profile_id}",
    )
    assert status == 405
    assert role_ui_app.role_profile_service.list_profiles() == profiles_before
    assert role_ui_app.bid_repository.list_audit(None) == audit_before


def test_gets_are_non_mutating_missing_is_404_and_delete_is_absent(
    role_ui_app: ModuleType,
) -> None:
    asyncio.run(_gets_are_non_mutating_missing_is_404_and_delete_is_absent(role_ui_app))


async def _json_routes_use_service_and_reject_server_owned_fields(
    role_ui_app: ModuleType,
) -> None:
    status, _, body = await _request(
        role_ui_app.app,
        "POST",
        "/api/ops/role-profiles",
        payload={
            "title": "JSON role",
            "mission": "JSON mission",
            "domains": ["CUSTOMER_SOLUTION"],
            "effective_from": "2026-09-01",
        },
    )
    assert status == 201
    profile_id = str(json.loads(body)["profile_id"])
    profile = role_ui_app.role_profile_service.get_profile(profile_id)
    assert profile.created_by == role_ui_app.LOCAL_ACTOR

    status, _, _ = await _request(
        role_ui_app.app,
        "POST",
        "/api/ops/role-profiles",
        payload={
            "profile_id": "RPF-client-controlled",
            "version_number": 99,
            "actor": "spoofed",
            "title": "Rejected",
            "effective_from": "2026-09-01",
        },
    )
    assert status == 422

    status, _, body = await _request(
        role_ui_app.app,
        "POST",
        f"/api/ops/role-profiles/{profile_id}/publish",
        payload={"expected_version_token": profile.version_token},
    )
    assert status == 200 and json.loads(body)["state"] == "PUBLISHED"
    published = role_ui_app.role_profile_service.get_profile(profile_id)
    status, _, list_body = await _request(
        role_ui_app.app,
        "GET",
        "/api/ops/role-profiles",
    )
    assert status == 200
    listed = json.loads(list_body)["profiles"][0]
    assert "domains_json" in listed and "domains" not in listed
    status, _, _ = await _request(
        role_ui_app.app,
        "POST",
        f"/api/ops/role-profiles/{profile_id}/retire",
        payload={"expected_version_token": "stale"},
    )
    assert status == 409
    assert role_ui_app.role_profile_service.get_profile(profile_id) == published


def test_json_routes_use_service_and_reject_server_owned_fields(
    role_ui_app: ModuleType,
) -> None:
    asyncio.run(_json_routes_use_service_and_reject_server_owned_fields(role_ui_app))


def test_role_profile_workflow_makes_no_network_call(role_ui_app: ModuleType) -> None:
    async def exercise() -> None:
        with patch("requests.sessions.Session.request", side_effect=AssertionError("network")):
            status, _, _ = await _request(role_ui_app.app, "GET", "/role-framework")
            assert status == 200
            status, _, _ = await _request(
                role_ui_app.app,
                "POST",
                "/role-framework",
                form={"title": "Local only", "effective_from": "2026-09-01"},
            )
            assert status == 303

    asyncio.run(exercise())
