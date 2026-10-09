"""The /decisions gate-approval form no longer records bid/no-bid as an approval.

Bid/no-bid is now the Bid's owner decision. A POST naming ``bid_no_bid`` is refused
before anything is written, the control is no longer offered on the page, and legacy
``bid_no_bid`` approval rows remain visible as history.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlencode, urlsplit

import pytest

from core.enums import ApprovalType
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
def decisions_bid_no_bid_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid_bid: Bid
) -> ModuleType:
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "decisions-bnb.db"))
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
        raise RuntimeError("Unable to load app.py for decisions bid/no-bid tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module._working_date = lambda: date(2026, 10, 1)
    module.bid_repository.create_bid(valid_bid)
    with module.db._conn() as conn:
        conn.execute(
            "INSERT INTO scenario_baselines VALUES(?,?,?,?,?,?)",
            (
                "SBL-DECISIONS-BNB",
                valid_bid.bid_id,
                "SV-DECISIONS-BNB",
                "jason",
                "2026-10-01T12:00:00+00:00",
                "Selected for decisions route test",
            ),
        )
        conn.commit()
    yield module
    sys.modules.pop("app", None)
    if previous is not None:
        sys.modules["app"] = previous


def _row_count(module: ModuleType, table: str) -> int:
    with module.db._conn() as conn:
        row = conn.execute(f"SELECT COUNT(*) count FROM {table}").fetchone()
    return int(row["count"])


def test_bid_no_bid_post_is_refused_and_writes_nothing(
    decisions_bid_no_bid_app: ModuleType, valid_bid: Bid
) -> None:
    module = decisions_bid_no_bid_app
    form = {
        "bid_id": valid_bid.bid_id,
        "approval_type": "bid_no_bid",
        "obtained": "on",
        "authority": "Executive sponsor",
        "evidence_ref": "Meeting minutes ref",
        "decision": "Do not pursue",
    }
    status, _, body = asyncio.run(_request(module.app, "POST", "/decisions/gate-approvals", form))
    page = body.decode()

    assert status == 422
    assert "owner decision" in page
    assert _row_count(module, "approvals") == 0
    assert _row_count(module, "audit_log") == 0


def test_margin_post_is_still_accepted(
    decisions_bid_no_bid_app: ModuleType, valid_bid: Bid
) -> None:
    module = decisions_bid_no_bid_app
    form = {
        "bid_id": valid_bid.bid_id,
        "approval_type": "margin",
        "obtained": "on",
        "authority": "Executive sponsor",
        "evidence_ref": "Meeting minutes ref",
        "decision": "Approved to bid at Level 2",
    }
    status, _, _ = asyncio.run(_request(module.app, "POST", "/decisions/gate-approvals", form))

    assert status == 303
    assert _row_count(module, "approvals") == 1
    assert _row_count(module, "audit_log") == 1


def test_decisions_get_bid_no_bid_falls_back_and_hides_option(
    decisions_bid_no_bid_app: ModuleType, valid_bid: Bid
) -> None:
    module = decisions_bid_no_bid_app
    status, _, body = asyncio.run(
        _request(
            module.app,
            "GET",
            f"/decisions?bid_id={valid_bid.bid_id}&approval_type=BID_NO_BID",
        )
    )
    page = body.decode()

    assert status == 200
    assert '<option value="bid_no_bid"' not in page
    # The requested control is not offered, so the page falls back to the first
    # offered control (margin) rather than crashing.
    assert '<option value="margin" selected>Margin</option>' in page


def test_legacy_bid_no_bid_row_still_listed_in_history(
    decisions_bid_no_bid_app: ModuleType, valid_bid: Bid
) -> None:
    module = decisions_bid_no_bid_app
    module.bid_repository.record_gate_approval(
        Approval(
            approval_id="APR-LEGACY-BNB-1",
            bid_id=valid_bid.bid_id,
            approval_type=ApprovalType.BID_NO_BID,
            obtained=True,
            authority="Legacy pursuit authority",
            evidence_ref="Legacy minute ref",
            decision="Do not pursue",
            decided_at=datetime.now(UTC),
            provenance=Provenance.from_human("Legacy actor"),
        ),
        "Legacy actor",
    )

    status, _, body = asyncio.run(
        _request(module.app, "GET", f"/decisions?bid_id={valid_bid.bid_id}")
    )
    page = body.decode()

    assert status == 200
    # The legacy row stays visible in the recorded-approvals history.
    assert "APR-LEGACY-BNB-1" in page
    assert "Legacy pursuit authority" in page
