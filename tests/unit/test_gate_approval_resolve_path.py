from __future__ import annotations

import asyncio
import html
import importlib.util
import re
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlencode, urlsplit

import pytest

from core.bid_control_center import _blocker_view, outstanding_items
from core.enums import ApprovalType, Gate
from core.gate_service import build_gate_context
from core.gates import ConditionState, evaluate_g4
from core.readiness import Blocker
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
def gate_approval_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid_bid: Bid
) -> ModuleType:
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "gate-approval.db"))
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
        raise RuntimeError("Unable to load app.py for gate approval tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module._working_date = lambda: date(2026, 10, 1)
    module.bid_repository.create_bid(valid_bid)
    with module.db._conn() as conn:
        conn.execute(
            "INSERT INTO scenario_baselines VALUES(?,?,?,?,?,?)",
            (
                "SBL-GATE-APPROVAL",
                valid_bid.bid_id,
                "SV-GATE-APPROVAL",
                "jason",
                "2026-10-01T12:00:00+00:00",
                "Selected for approval tests",
            ),
        )
        conn.commit()
    yield module
    sys.modules.pop("app", None)
    if previous is not None:
        sys.modules["app"] = previous


def _blocker(condition_id: str, gate: Gate) -> Blocker:
    return Blocker(
        condition_id=condition_id,
        gate=gate,
        description="Required approval has been obtained.",
        detail="Required approval has not been obtained.",
        material=True,
    )


def _row_count(module: ModuleType, table: str) -> int:
    with module.db._conn() as conn:
        row = conn.execute(f"SELECT COUNT(*) count FROM {table}").fetchone()
    return int(row["count"])


def test_gate_approval_blockers_have_direct_destinations_and_labels(valid_bid: Bid) -> None:
    owner_decision = _blocker_view(valid_bid, _blocker("g1.owner_pursuit_decided", Gate.G1))
    margin = _blocker_view(valid_bid, _blocker("g4.margin_approved", Gate.G4))
    required = _blocker_view(valid_bid, _blocker("g4.required_approvals", Gate.G4))

    assert owner_decision.destination == f"/bids/{valid_bid.bid_id}/owner-decision"
    assert margin.destination == (
        f"/decisions?bid_id={valid_bid.bid_id}&approval_type=MARGIN#gate-approval"
    )
    assert required.destination == f"/decisions?bid_id={valid_bid.bid_id}#gate-approval"
    items = outstanding_items([owner_decision, margin])
    assert [item.resolve_label for item in items] == [
        "Record owner decision",
        "Record margin approval",
    ]


def test_decisions_get_preselects_control_and_renders_bid_context(
    gate_approval_app: ModuleType, valid_bid: Bid
) -> None:
    status, _, body = asyncio.run(
        _request(
            gate_approval_app.app,
            "GET",
            f"/decisions?bid_id={valid_bid.bid_id}&approval_type=MARGIN",
        )
    )
    page = body.decode()

    assert status == 200
    assert '<option value="margin" selected>Margin</option>' in page
    assert valid_bid.project_name in page
    assert valid_bid.bid_id in page
    assert "Level 2" in page
    assert "The person or role who made the decision" in page
    assert "Where the record of the decision lives" in page
    assert "The outcome in words" in page
    assert "Policy attention below applies to decision cases" in page
    invalid_status, _, _ = asyncio.run(
        _request(
            gate_approval_app.app,
            "GET",
            f"/decisions?bid_id={valid_bid.bid_id}&approval_type=NOT_A_CONTROL",
        )
    )
    assert invalid_status == 200


def test_decisions_get_does_not_write(gate_approval_app: ModuleType, valid_bid: Bid) -> None:
    module = gate_approval_app
    before = (_row_count(module, "approvals"), _row_count(module, "audit_log"))

    status, _, _ = asyncio.run(
        _request(
            module.app,
            "GET",
            f"/decisions?bid_id={valid_bid.bid_id}&approval_type=MARGIN",
        )
    )

    assert status == 200
    assert (_row_count(module, "approvals"), _row_count(module, "audit_log")) == before


def test_margin_approval_resolve_round_trip_and_idempotent_resubmission(
    gate_approval_app: ModuleType, valid_bid: Bid
) -> None:
    module = gate_approval_app
    bid_status, _, bid_body = asyncio.run(_request(module.app, "GET", f"/bids/{valid_bid.bid_id}"))
    assert bid_status == 200
    match = re.search(
        r'<a class="button" href="([^"]+)">Record margin approval</a>',
        bid_body.decode(),
    )
    assert match is not None
    resolve_href = html.unescape(match.group(1))
    assert resolve_href.endswith("&approval_type=MARGIN#gate-approval")

    approval_status, _, approval_body = asyncio.run(_request(module.app, "GET", resolve_href))
    assert approval_status == 200
    assert '<option value="margin" selected>Margin</option>' in approval_body.decode()
    form = {
        "bid_id": valid_bid.bid_id,
        "approval_type": ApprovalType.MARGIN.value,
        "obtained": "on",
        "authority": "Executive sponsor",
        "evidence_ref": "Meeting minutes ref",
        "decision": "Approved to bid at Level 2",
    }
    created_status, created_headers, _ = asyncio.run(
        _request(module.app, "POST", "/decisions/gate-approvals", form)
    )
    assert created_status == 303
    approvals = module.bid_repository.list_approvals(valid_bid.bid_id)
    audits = module.bid_repository.list_audit(valid_bid.bid_id)
    assert len(approvals) == 1
    assert approvals[0].approval_type is ApprovalType.MARGIN
    assert approvals[0].obtained is True
    assert len(audits) == 1
    assert audits[0].action == "bid_gate_approval_created"
    saved_status, _, saved_body = asyncio.run(
        _request(module.app, "GET", created_headers["location"])
    )
    assert saved_status == 200
    assert b"Margin approval is recorded as obtained" in saved_body
    assert b"Meeting minutes ref" in saved_body
    assert b"Approved to bid at Level 2" in saved_body

    g4 = evaluate_g4(build_gate_context(module.bid_repository, module.db, valid_bid.bid_id))
    margin_condition = next(
        condition for condition in g4.conditions if condition.condition_id == "g4.margin_approved"
    )
    assert margin_condition.state is ConditionState.MET

    duplicate_status, _, _ = asyncio.run(
        _request(module.app, "POST", "/decisions/gate-approvals", form)
    )
    # An identical resubmission is idempotent: it succeeds and writes nothing further.
    assert duplicate_status == 303
    assert _row_count(module, "approvals") == 1
    assert _row_count(module, "audit_log") == 1
