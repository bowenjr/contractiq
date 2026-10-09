from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import date
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
    form: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    sent: list[dict[str, Any]] = []
    incoming = [
        {
            "type": "http.request",
            "body": urlencode(form or {}).encode(),
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
    return int(start["status"]), headers, body


@pytest.fixture
def pricing_ui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid_bid: Bid) -> ModuleType:
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "pricing-ui.db"))
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
        raise RuntimeError("Unable to load app.py for pricing baseline UI tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module._working_date = lambda: date(2026, 10, 2)
    module.bid_repository.create_bid(valid_bid)
    yield module
    sys.modules.pop("app", None)
    if previous is not None:
        sys.modules["app"] = previous


def _family_form(return_to: str = "") -> dict[str, str]:
    return {
        "code": "BASE-UI",
        "purpose": "BASE",
        "title": "Browser-entered base pricing",
        "owner": "Commercial owner",
        "intent": "Authoritative pricing for senior approval",
        "return_to": return_to,
    }


def _version_form(family_id: str, return_to: str = "") -> dict[str, str]:
    return {
        "family_id": family_id,
        "revenue_role": "CUSTOMER_REVENUE",
        "revenue_amount": "100000.00",
        "revenue_currency": "CAD",
        "cost_role": "DIRECT_PRODUCT_COST",
        "cost_amount": "70000.00",
        "cost_currency": "CAD",
        "cost_role_2": "PROJECT_DELIVERY_COST",
        "cost_amount_2": "5000.00",
        "cost_currency_2": "CAD",
        "return_to": return_to,
    }


def _create_family_and_version(module: ModuleType, bid_id: str, *, return_to: str = "") -> str:
    status, _, _ = asyncio.run(
        _request(
            module.app,
            "POST",
            f"/bids/{bid_id}/pricing-baseline/families",
            _family_form(return_to),
        )
    )
    assert status == 303
    family_id = str(module.scenario_repository.families(bid_id)[0]["family_id"])
    status, _, _ = asyncio.run(
        _request(
            module.app,
            "POST",
            f"/bids/{bid_id}/pricing-baseline/versions",
            _version_form(family_id, return_to),
        )
    )
    assert status == 303
    return str(module.scenario_repository.versions(family_id)[0]["scenario_version_id"])


def test_browser_flow_selects_baseline_and_senior_approval_binds_to_it(
    pricing_ui_app: ModuleType, valid_bid: Bid
) -> None:
    module = pricing_ui_app
    bid_id = valid_bid.bid_id
    approval_return = f"/decisions?bid_id={bid_id}&approval_type=MARGIN#gate-approval"

    status, _, body = asyncio.run(
        _request(module.app, "GET", f"/decisions?bid_id={bid_id}&approval_type=MARGIN")
    )
    page = body.decode()
    assert status == 200
    assert "No pricing baseline is selected" in page
    assert "Set the pricing baseline and return to this approval" in page
    assert f"/bids/{bid_id}/pricing-baseline?return_to=" in page
    assert '<form method="post" action="/decisions/gate-approvals">' not in page

    version_id = _create_family_and_version(module, bid_id, return_to=approval_return)
    status, _, _ = asyncio.run(
        _request(
            module.app,
            "POST",
            f"/bids/{bid_id}/pricing-baseline/versions/{version_id}/reviews",
            {
                "decision": "ACCEPTED",
                "reviewer": "Independent reviewer",
                "rationale": "Calculation and source amounts checked",
                "return_to": approval_return,
            },
        )
    )
    assert status == 303

    status, headers, _ = asyncio.run(
        _request(
            module.app,
            "POST",
            f"/bids/{bid_id}/pricing-baseline/select",
            {
                "scenario_version_id": version_id,
                "baseline_rationale": "Approved basis for this senior decision",
                "return_to": approval_return,
            },
        )
    )
    assert status == 303
    assert headers["location"] == approval_return
    selection = module.scenario_repository.baselines(bid_id)[0]

    status, _, body = asyncio.run(_request(module.app, "GET", f"/bids/{bid_id}/pricing-baseline"))
    baseline_page = body.decode()
    assert status == 200
    assert str(selection["selection_id"]) in baseline_page
    assert "CAD 75000.00" in baseline_page
    assert "CAD 25000.00" in baseline_page
    assert "Approved basis for this senior decision" in baseline_page

    status, _, body = asyncio.run(_request(module.app, "GET", f"/bids/{bid_id}"))
    bid_page = body.decode()
    assert status == 200
    assert "Pricing baseline:</b> Selected — BASE-UI version 1" in bid_page
    assert str(selection["selection_id"]) in bid_page

    status, _, body = asyncio.run(_request(module.app, "GET", approval_return))
    page = body.decode()
    assert status == 200
    assert "Pricing baseline for this approval" in page
    assert "BASE-UI" in page
    assert "CAD 100000.00 revenue" in page
    assert "25.00% margin" in page
    assert str(selection["selection_id"]) in page

    status, _, _ = asyncio.run(
        _request(
            module.app,
            "POST",
            "/decisions/gate-approvals",
            {
                "bid_id": bid_id,
                "approval_type": "margin",
                "obtained": "on",
                "authority": "Executive sponsor",
                "evidence_ref": "Approval minutes 2026-10-02",
                "decision": "Approved against BASE-UI version 1",
            },
        )
    )
    assert status == 303
    approval = module.bid_repository.list_current_approvals(bid_id)[0]
    assert approval.pricing_baseline_id == str(selection["selection_id"])


def test_reviewer_must_differ_from_browser_actor(
    pricing_ui_app: ModuleType, valid_bid: Bid
) -> None:
    module = pricing_ui_app
    version_id = _create_family_and_version(module, valid_bid.bid_id)

    status, _, body = asyncio.run(
        _request(
            module.app,
            "POST",
            f"/bids/{valid_bid.bid_id}/pricing-baseline/versions/{version_id}/reviews",
            {
                "decision": "ACCEPTED",
                "reviewer": module.LOCAL_ACTOR,
                "rationale": "Self review must be refused",
            },
        )
    )
    page = body.decode()
    assert status == 422
    assert "scenario author cannot independently review" in page
    assert "Enter an independent reviewer" in page
    assert module.scenario_repository.reviews(version_id) == []


def test_unreviewed_version_cannot_be_selected(pricing_ui_app: ModuleType, valid_bid: Bid) -> None:
    module = pricing_ui_app
    version_id = _create_family_and_version(module, valid_bid.bid_id)

    status, _, body = asyncio.run(
        _request(
            module.app,
            "POST",
            f"/bids/{valid_bid.bid_id}/pricing-baseline/select",
            {
                "scenario_version_id": version_id,
                "baseline_rationale": "Attempt before review",
            },
        )
    )
    page = body.decode()
    assert status == 422
    assert "baseline requires independent calculation acceptance" in page
    assert "Record an ACCEPTED review" in page
    assert module.scenario_repository.baselines(valid_bid.bid_id) == []


def test_external_return_to_is_ignored_for_baseline_redirect(
    pricing_ui_app: ModuleType, valid_bid: Bid
) -> None:
    module = pricing_ui_app
    bid_id = valid_bid.bid_id
    unsafe_return = "//evil.example/steal"
    version_id = _create_family_and_version(module, bid_id, return_to=unsafe_return)
    status, _, _ = asyncio.run(
        _request(
            module.app,
            "POST",
            f"/bids/{bid_id}/pricing-baseline/versions/{version_id}/reviews",
            {
                "decision": "ACCEPTED",
                "reviewer": "Independent reviewer",
                "rationale": "Reviewed",
                "return_to": unsafe_return,
            },
        )
    )
    assert status == 303

    status, headers, _ = asyncio.run(
        _request(
            module.app,
            "POST",
            f"/bids/{bid_id}/pricing-baseline/select",
            {
                "scenario_version_id": version_id,
                "baseline_rationale": "Selected without trusting an external redirect",
                "return_to": unsafe_return,
            },
        )
    )
    assert status == 303
    assert headers["location"] == f"/bids/{bid_id}/pricing-baseline"

    status, _, body = asyncio.run(
        _request(
            module.app,
            "GET",
            f"/bids/{bid_id}/pricing-baseline?{urlencode({'return_to': unsafe_return})}",
        )
    )
    assert status == 200
    assert unsafe_return not in body.decode()
