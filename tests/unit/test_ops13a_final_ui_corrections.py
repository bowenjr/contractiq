"""Focused route and markup coverage for the OPS-13A final UI corrections."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlencode, urlsplit

import pytest

from core.schemas import Bid


async def _request(
    application: Any,
    method: str,
    path: str,
    form: dict[str, str | list[str]] | None = None,
) -> tuple[int, dict[str, str], str]:
    sent: list[dict[str, Any]] = []
    incoming = [
        {
            "type": "http.request",
            "body": urlencode(form or {}, doseq=True).encode(),
            "more_body": False,
        }
    ]

    async def receive() -> dict[str, Any]:
        return incoming.pop(0) if incoming else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    parsed = urlsplit(path)
    await application(
        {
            "type": "http",
            "method": method,
            "path": parsed.path,
            "query_string": parsed.query.encode(),
            "scheme": "http",
            "server": ("127.0.0.1", 0),
            "client": ("127.0.0.1", 1),
            "http_version": "1.1",
            "headers": (
                [(b"content-type", b"application/x-www-form-urlencoded")]
                if method == "POST"
                else []
            ),
        },
        receive,
        send,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    headers = {
        bytes(key).decode().lower(): bytes(value).decode() for key, value in start["headers"]
    }
    body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return int(start["status"]), headers, body.decode()


@pytest.fixture
def final_ui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid_bid: Bid) -> ModuleType:
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "ops13a-final-ui.db"))
    monkeypatch.setenv("CONTRACTIQ_DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setenv("CONTRACTIQ_PROPOSAL_ARTIFACT_ROOT", str(tmp_path / "proposal"))
    monkeypatch.setenv("CONTRACTIQ_INTAKE_STORAGE_ROOT", str(tmp_path / "intake"))
    monkeypatch.setenv("CONTRACTIQ_DISCOVERY_RUNNER", "off")
    monkeypatch.delenv("CONTRACTIQ_INTAKE_ROOTS", raising=False)
    monkeypatch.delenv("CONTRACTIQ_INTAKE_SOURCE_ROOTS", raising=False)
    previous = sys.modules.pop("app", None)
    app_path = Path(__file__).parents[2] / "app.py"
    spec = importlib.util.spec_from_file_location("app", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load app.py for final UI tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module.bid_repository.create_bid(valid_bid)
    yield module
    sys.modules.pop("app", None)
    if previous is not None:
        sys.modules["app"] = previous


def _policy_count(module: ModuleType) -> int:
    with module.db._conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM authority_policies").fetchone()[0])


def test_owner_decision_blockers_link_to_form_and_return_safely(
    final_ui_app: ModuleType, valid_bid: Bid
) -> None:
    status, _, page = asyncio.run(
        _request(
            final_ui_app.app,
            "GET",
            f"/bids/{valid_bid.bid_id}/proposal-issue-control",
        )
    )

    assert status == 200
    owner_path = f"/bids/{valid_bid.bid_id}/owner-decision?return="
    assert owner_path in page
    assert "Record owner decision" in page
    assert "Correct this authoritative fact" not in page
    assert "Configure approval policy" in page

    return_path = f"/bids/{valid_bid.bid_id}/proposal-issue-control"
    owner_status, _, owner_page = asyncio.run(
        _request(
            final_ui_app.app,
            "GET",
            f"/bids/{valid_bid.bid_id}/owner-decision?{urlencode({'return': return_path})}",
        )
    )
    assert owner_status == 200
    assert f'href="{return_path}"' in owner_page
    assert f'name="return" value="{return_path}"' in owner_page

    _, _, unsafe_page = asyncio.run(
        _request(
            final_ui_app.app,
            "GET",
            f"/bids/{valid_bid.bid_id}/owner-decision?"
            + urlencode({"return": "https://example.invalid/steal"}),
        )
    )
    assert 'href="https://example.invalid/steal"' not in unsafe_page
    assert f'href="/bids/{valid_bid.bid_id}"' in unsafe_page


def _ordinary_policy_form(bid_id: str, **overrides: str | list[str]) -> dict[str, str | list[str]]:
    """Inputs of the ordinary policy form: no JSON anywhere."""
    form: dict[str, str | list[str]] = {
        "bid_id": bid_id,
        "name": "Retained & reviewed policy",
        "description": "Entered description",
        "scope": "Bid issue",
        "effective_from": "2026-10-01T09:00",
        "effective_until": "",
        "approval_controls": ["margin"],
        "other_authorities": "Legal Director",
    }
    form.update(overrides)
    return form


def _policy_row(module: ModuleType) -> Any:
    with module.db._conn() as conn:
        return conn.execute("SELECT * FROM authority_policies").fetchone()


def test_ordinary_policy_form_creates_publishes_and_covers_level2_approval(
    final_ui_app: ModuleType, valid_bid: Bid
) -> None:
    from core.senior_approvals import _approval_is_policy_covered

    module = final_ui_app
    _, _, page = asyncio.run(_request(module.app, "GET", f"/decisions?bid_id={valid_bid.bid_id}"))
    assert _policy_count(module) == 0
    assert "No company policy is active by default" in page
    assert 'action="/decisions/authority-policies"' in page
    assert "Advanced policy configuration" in page
    assert page.count('name="approval_controls"') == 5
    assert "checked" not in page.split('name="approval_controls"')[1].split("</label>")[0]
    advanced = page.split("Advanced policy configuration")[1].split("</details>")[0]
    assert 'name="rules"' in advanced and 'name="stages"' in advanced
    ordinary = page.split("Advanced policy configuration")[0]
    assert 'name="rules"' not in ordinary.split('id="authority-policies"')[1]

    status, headers, _ = asyncio.run(
        _request(
            module.app,
            "POST",
            "/decisions/authority-policies",
            _ordinary_policy_form(valid_bid.bid_id),
        )
    )
    assert status == 303 and "policy_notice=created" in headers["location"]
    policy = _policy_row(module)
    assert policy["lifecycle_state"] == "DRAFT"
    assert json.loads(policy["roles_json"]) == ["margin", "Legal Director"]
    # No rules, stages, thresholds or approvers are invented behind the ordinary form.
    assert json.loads(policy["rules_json"]) == [] and json.loads(policy["stages_json"]) == []
    assert not module._has_effective_authority_policy()

    publish_status, publish_headers, _ = asyncio.run(
        _request(
            module.app,
            "POST",
            f"/decisions/authority-policies/{policy['policy_id']}/publish",
            {"bid_id": valid_bid.bid_id},
        )
    )
    assert publish_status == 303 and "policy_notice=published" in publish_headers["location"]
    with module.db._conn() as conn:
        audit = conn.execute("SELECT action FROM audit_log WHERE action='policy_published'")
        assert len(audit.fetchall()) == 1
    assert module._has_effective_authority_policy()
    policies = module.approval_repository.policies()
    assert _approval_is_policy_covered({"approval_type": "margin", "authority": "x"}, policies)
    assert _approval_is_policy_covered(
        {"approval_type": "executive", "authority": "legal director"}, policies
    )
    assert not _approval_is_policy_covered({"approval_type": "credit", "authority": "x"}, policies)


def test_ordinary_policy_form_retains_every_input_on_error(
    final_ui_app: ModuleType, valid_bid: Bid
) -> None:
    module = final_ui_app
    bad = _ordinary_policy_form(
        valid_bid.bid_id,
        approval_controls=["legal", "credit"],
        other_authorities="<Finance approver>",
        effective_from="",
        rules="[{not json",
    )
    status, _, page = asyncio.run(
        _request(module.app, "POST", "/decisions/authority-policies", bad)
    )
    assert status == 422 and _policy_count(module) == 0
    assert "Retained &amp; reviewed policy" in page and "&lt;Finance approver&gt;" in page
    assert "Rules must be valid JSON" in page and "[{not json" in page
    for control in ("legal", "credit"):
        assert f'value="{control}" checked' in page
    assert 'value="margin" checked' not in page
    assert "Advanced policy configuration" in page

    no_authority = _ordinary_policy_form(
        valid_bid.bid_id, approval_controls=[], other_authorities=""
    )
    status, _, page = asyncio.run(
        _request(module.app, "POST", "/decisions/authority-policies", no_authority)
    )
    assert status == 422 and _policy_count(module) == 0
    assert "Choose at least one approval control" in page

    unknown = _ordinary_policy_form(valid_bid.bid_id, approval_controls=["bid_no_bid"])
    status, _, page = asyncio.run(
        _request(module.app, "POST", "/decisions/authority-policies", unknown)
    )
    assert status == 422 and _policy_count(module) == 0
    assert "Unknown approval control" in page


def test_advanced_json_and_unsupported_dimensions_are_preserved(
    final_ui_app: ModuleType, valid_bid: Bid
) -> None:
    module = final_ui_app
    rules = '[{"rule_id":"R-1","dimensions":{"unsupported_dimension":"kept"}}]'
    stages = '[{"order":1,"mode":"ANY_ONE","roles":["margin","Legal Director"]}]'
    status, _, _ = asyncio.run(
        _request(
            module.app,
            "POST",
            "/decisions/authority-policies",
            _ordinary_policy_form(valid_bid.bid_id, rules=rules, stages=stages),
        )
    )
    assert status == 303
    policy = _policy_row(module)
    assert json.loads(policy["rules_json"]) == json.loads(rules)
    assert json.loads(policy["stages_json"]) == json.loads(stages)

    # A stage may only use roles declared above; the error says how to fix it.
    bad_stage = '[{"order":1,"mode":"ALL_REQUIRED","roles":["undeclared"]}]'
    status, _, page = asyncio.run(
        _request(
            module.app,
            "POST",
            "/decisions/authority-policies",
            _ordinary_policy_form(valid_bid.bid_id, rules=rules, stages=bad_stage),
        )
    )
    assert status == 422 and "undeclared" in page and "tick or enter them above" in page

    # Starting a new draft from the stored policy carries unsupported dimensions verbatim.
    status, _, page = asyncio.run(
        _request(
            module.app,
            "GET",
            f"/decisions?bid_id={valid_bid.bid_id}&copy_policy={policy['policy_id']}",
        )
    )
    assert status == 200
    assert "unsupported_dimension" in page and "kept" in page
    assert 'value="margin" checked' in page and "Legal Director" in page
    assert 'name="effective_from"' in page and 'value="2026-10-01' not in page


def test_stopped_bid_hides_preparation_and_export_actions(
    final_ui_app: ModuleType, valid_bid: Bid
) -> None:
    reason = "Owner stopped pursuit after the commercial review."
    final_ui_app.owner_decision_service.record_owner_decision(
        valid_bid.bid_id,
        "DO_NOT_PURSUE",
        reason,
        "Jason",
    )

    status, _, page = asyncio.run(
        _request(
            final_ui_app.app,
            "GET",
            f"/bids/{valid_bid.bid_id}/proposal-issue-control",
        )
    )
    assert status == 200
    assert reason in page
    assert "Proposal work is stopped" in page
    assert 'action="/bids/' + valid_bid.bid_id + '/proposal-exports"' not in page
    assert "Prepare and download package" not in page
    assert '/download"' not in page
    assert "Record owner decision" in page
    assert f"/bids/{valid_bid.bid_id}/owner-decision?return=" in page


def test_bulk_review_markup_keeps_only_count_and_apply_in_sticky_bar() -> None:
    root = Path(__file__).parents[2]
    template = (root / "templates" / "_package_intake_section.html").read_text()
    start = template.index('<div class="bulk-review-bar">')
    end = template.index("</div>", start) + len("</div>")
    sticky = template[start:end]

    assert "data-bulk-selected-count" in sticky
    assert "data-bulk-submit" in sticky
    assert "data-select-all-files" not in sticky
    assert "analysis_eligibility" not in sticky
    assert "content_form" not in sticky
    assert "data-bulk-fields" in template[:start]
    assert "data-bulk-exclusion" in template
    assert "reason.hidden = !excluded" in template
