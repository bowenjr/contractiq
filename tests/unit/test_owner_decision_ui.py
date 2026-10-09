"""UI tests for the Bid owner decision page and its recording route."""

from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlencode, urlsplit

import pytest

from core.enums import ApprovalType, BidLevel
from core.schemas import Approval, Bid, Provenance


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
def owner_decision_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid_bid: Bid
) -> ModuleType:
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "owner-decision.db"))
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
        raise RuntimeError("Unable to load app.py for owner decision tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module._working_date = lambda: date(2026, 10, 1)
    module.bid_repository.create_bid(valid_bid)
    yield module
    sys.modules.pop("app", None)
    if previous is not None:
        sys.modules["app"] = previous


def _rows(module: ModuleType, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(module.db.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def _decision_rows(module: ModuleType, bid_id: str) -> list[sqlite3.Row]:
    return _rows(
        module,
        "SELECT * FROM bid_owner_decisions WHERE bid_id=? ORDER BY decided_at, decision_id",
        (bid_id,),
    )


def _bid_status(module: ModuleType, bid_id: str) -> str:
    row = _rows(module, "SELECT status FROM bids WHERE bid_id=?", (bid_id,))[0]
    return str(row["status"])


def _post_decision(
    module: ModuleType, bid_id: str, form: dict[str, str]
) -> tuple[int, dict[str, str], bytes]:
    return asyncio.run(_request(module.app, "POST", f"/bids/{bid_id}/owner-decision", form))


def test_owner_decision_get_shows_form_without_legacy_fields(
    owner_decision_app: ModuleType, valid_bid: Bid
) -> None:
    module = owner_decision_app
    status, _, body = asyncio.run(
        _request(module.app, "GET", f"/bids/{valid_bid.bid_id}/owner-decision")
    )
    page = body.decode()

    assert status == 200
    assert 'name="reason"' in page
    assert 'value="PURSUE"' in page
    assert 'value="DO_NOT_PURSUE"' in page
    assert "No owner decision recorded" in page
    assert 'name="authority"' not in page
    assert 'name="evidence_ref"' not in page
    assert 'name="obtained"' not in page


def test_owner_decision_post_pursue_records_decision_and_redirects(
    owner_decision_app: ModuleType, valid_bid: Bid
) -> None:
    module = owner_decision_app
    status, headers, _ = _post_decision(
        module, valid_bid.bid_id, {"decision": "PURSUE", "reason": "Strong fit"}
    )

    assert status == 303
    assert headers["location"] == f"/bids/{valid_bid.bid_id}/owner-decision?recorded=1"
    decisions = _decision_rows(module, valid_bid.bid_id)
    assert len(decisions) == 1
    assert str(decisions[0]["decision"]) == "PURSUE"
    assert str(decisions[0]["reason"]) == "Strong fit"
    audits = _rows(
        module,
        "SELECT action FROM audit_log WHERE bid_id=? AND action='owner_pursuit_decision_recorded'",
        (valid_bid.bid_id,),
    )
    assert len(audits) == 1

    follow_status, _, follow_body = asyncio.run(_request(module.app, "GET", headers["location"]))
    page = follow_body.decode()
    assert follow_status == 200
    assert "Decision recorded." in page
    assert "Strong fit" in page


def test_owner_decision_post_do_not_pursue_stops_the_bid(
    owner_decision_app: ModuleType, valid_bid: Bid
) -> None:
    module = owner_decision_app
    status, headers, _ = _post_decision(
        module,
        valid_bid.bid_id,
        {"decision": "DO_NOT_PURSUE", "reason": "Margins are not viable"},
    )

    assert status == 303
    assert _bid_status(module, valid_bid.bid_id) == "no_bid"
    follow_status, _, follow_body = asyncio.run(_request(module.app, "GET", headers["location"]))
    page = follow_body.decode()
    assert follow_status == 200
    assert (
        "This Bid is stopped by the owner decision. "
        "Record a new Pursue decision with a reason to reopen it." in page
    )


def test_g5_stopped_workspace_and_history_render_owner_reason_and_reopen_action(
    owner_decision_app: ModuleType, valid_bid: Bid
) -> None:
    module = owner_decision_app
    status, _, _ = _post_decision(
        module,
        valid_bid.bid_id,
        {"decision": "DO_NOT_PURSUE", "reason": "Commercial exposure is unacceptable"},
    )
    assert status == 303

    workspace_status, _, workspace_body = asyncio.run(
        _request(module.app, "GET", f"/bids/{valid_bid.bid_id}")
    )
    workspace_page = workspace_body.decode()
    assert workspace_status == 200
    assert "Stopped by owner decision — Commercial exposure is unacceptable" in workspace_page
    assert f'href="/bids/{valid_bid.bid_id}/owner-decision"' in workspace_page
    assert workspace_page.count(">Record a new decision</a>") >= 1
    assert "Not applicable" in workspace_page

    history_status, _, history_body = asyncio.run(_request(module.app, "GET", "/bids?view=history"))
    history_page = history_body.decode()
    assert history_status == 200
    assert "Stopped by owner decision" in history_page
    assert "Commercial exposure is unacceptable" in history_page


def test_workspace_renders_issue_phase_margin_as_before_issue(
    owner_decision_app: ModuleType, valid_bid: Bid
) -> None:
    module = owner_decision_app
    module.bid_repository.update_bid(
        valid_bid.model_copy(update={"classification": BidLevel.LEVEL_2})
    )

    status, _, body = asyncio.run(_request(module.app, "GET", f"/bids/{valid_bid.bid_id}"))
    page = body.decode()
    assert status == 200
    assert "pending before issue" in page
    assert "Before issue" in page
    assert "Record margin approval" in page
    assert "approval_type=MARGIN#gate-approval" in page


def test_owner_decision_post_same_decision_twice_is_rejected(
    owner_decision_app: ModuleType, valid_bid: Bid
) -> None:
    module = owner_decision_app
    first_status, _, _ = _post_decision(
        module, valid_bid.bid_id, {"decision": "PURSUE", "reason": "Strong fit"}
    )
    assert first_status == 303
    first_id = str(_decision_rows(module, valid_bid.bid_id)[0]["decision_id"])

    second_status, _, second_body = _post_decision(
        module,
        valid_bid.bid_id,
        {
            "decision": "PURSUE",
            "reason": "Still a strong fit",
            "expected_current_decision_id": first_id,
        },
    )

    assert second_status == 422
    assert "already the current decision" in second_body.decode()
    assert len(_decision_rows(module, valid_bid.bid_id)) == 1


def test_owner_decision_post_stale_expected_id_is_rejected(
    owner_decision_app: ModuleType, valid_bid: Bid
) -> None:
    module = owner_decision_app
    first_status, _, _ = _post_decision(
        module, valid_bid.bid_id, {"decision": "PURSUE", "reason": "Strong fit"}
    )
    assert first_status == 303

    second_status, _, _ = _post_decision(
        module,
        valid_bid.bid_id,
        {
            "decision": "DO_NOT_PURSUE",
            "reason": "Stop the bid",
            "expected_current_decision_id": "OD-stale",
        },
    )

    assert second_status == 422
    assert len(_decision_rows(module, valid_bid.bid_id)) == 1


def test_owner_decision_post_empty_reason_is_rejected(
    owner_decision_app: ModuleType, valid_bid: Bid
) -> None:
    module = owner_decision_app
    status, _, _ = _post_decision(module, valid_bid.bid_id, {"decision": "PURSUE", "reason": ""})

    assert status == 422
    assert len(_decision_rows(module, valid_bid.bid_id)) == 0


def test_owner_decision_get_shows_legacy_approval_without_changing_status(
    owner_decision_app: ModuleType, valid_bid: Bid, valid_provenance: Provenance
) -> None:
    module = owner_decision_app
    module.bid_repository.create_approval(
        Approval(
            approval_id="APP-bid_no_bid",
            bid_id=valid_bid.bid_id,
            approval_type=ApprovalType.BID_NO_BID,
            required=True,
            obtained=True,
            decision="Do not pursue",
            provenance=valid_provenance,
        )
    )

    status, _, body = asyncio.run(
        _request(module.app, "GET", f"/bids/{valid_bid.bid_id}/owner-decision")
    )
    page = body.decode()

    assert status == 200
    assert "Legacy approval record — not an owner decision" in page
    assert "No owner decision recorded" in page
    assert _bid_status(module, valid_bid.bid_id) == "active"


def test_owner_decision_do_not_pursue_then_pursue_reopens_the_bid(
    owner_decision_app: ModuleType, valid_bid: Bid
) -> None:
    module = owner_decision_app
    first_status, _, _ = _post_decision(
        module, valid_bid.bid_id, {"decision": "DO_NOT_PURSUE", "reason": "Stop the bid"}
    )
    assert first_status == 303
    first_id = str(_decision_rows(module, valid_bid.bid_id)[0]["decision_id"])

    second_status, _, _ = _post_decision(
        module,
        valid_bid.bid_id,
        {
            "decision": "PURSUE",
            "reason": "Reopen the bid",
            "expected_current_decision_id": first_id,
        },
    )

    assert second_status == 303
    assert _bid_status(module, valid_bid.bid_id) == "active"
    decisions = _decision_rows(module, valid_bid.bid_id)
    assert len(decisions) == 2
    assert [str(row["decision"]) for row in decisions] == ["DO_NOT_PURSUE", "PURSUE"]
    assert str(decisions[1]["supersedes_decision_id"]) == first_id


def test_owner_decision_unknown_bid_is_404(owner_decision_app: ModuleType) -> None:
    module = owner_decision_app
    get_status, _, _ = asyncio.run(
        _request(module.app, "GET", "/bids/B-DOES-NOT-EXIST/owner-decision")
    )
    assert get_status == 404
    post_status, _, _ = _post_decision(
        module, "B-DOES-NOT-EXIST", {"decision": "PURSUE", "reason": "Strong fit"}
    )
    assert post_status == 404


def test_stopped_bid_issue_control_does_not_offer_a_draft_notice(
    owner_decision_app: ModuleType, valid_bid: Bid
) -> None:
    """A stopped Bid cannot draft, so the issue-control page must not say it can."""
    module = owner_decision_app
    status, _, _ = asyncio.run(
        _request(
            module.app,
            "POST",
            f"/bids/{valid_bid.bid_id}/owner-decision",
            {
                "decision": "DO_NOT_PURSUE",
                "reason": "Not a fit",
                "expected_current_decision_id": "",
            },
        )
    )
    assert status == 303
    status, _, body = asyncio.run(
        _request(module.app, "GET", f"/bids/{valid_bid.bid_id}/proposal-issue-control")
    )
    page = body.decode()
    assert status == 200
    assert "Draft only" not in page
    assert "stopped by owner decision" in page.lower()
