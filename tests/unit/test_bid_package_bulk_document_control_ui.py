"""ASGI-level acceptance for the post-import review / document-control workflow fix.

Reproduces Jason's exact report — a 16-file package where "3 of 16 received files have not
been reviewed yet" and "13 reviewed files are not yet under document control" read as 13
mandatory one-by-one actions — and proves, through the real HTTP routes and rendered pages:

1. Review-decisions-remaining and governing-sources-pending are reported as separate facts,
   never merged into one "still to do" list.
2. The bulk document-control action (`/bulk-controlled-documents`) puts every selected,
   already-included file under document control in one action, reusing the managed originals
   — no re-upload, no shared document identity across files.
3. Once review is complete but document control is still pending, the page never claims the
   package is "ready"; once both are done, it does, with a direct, reachable next action.
4. The real gate (`BidPackageRepository._release_incorporation_issues`) and OPS-13A discovery
   route are exercised for real, not just described.

No real customer files are used — every uploaded file is synthetic bytes built at test time.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from core.enums import BidLevel, BidStatus, CustomerType, Gate
from core.schemas import Bid
from scripts.asgi_acceptance_ops08 import request
from tests.unit.deliverable_discovery_job_route_helpers import post_run_and_finish

BID_ID = "B-2026-4444"


class _StubLLM:
    """Deterministic, network-free stand-in for the local LLM — proposes nothing.

    This test is about the document-control/Bid-Basis/discovery-gate wiring, not candidate
    quality (already covered by `test_deliverable_discovery_llm.py`/`_service.py`), so it
    always returns zero candidates rather than reaching the real, possibly-unreachable
    llama.cpp endpoint.
    """

    def complete_json(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.05,
        max_tokens: int = 1024,
        context_label: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, object]:
        return {"candidates": []}


async def _post_form(
    app: ModuleType, path: str, pairs: list[tuple[str, str]]
) -> tuple[int, dict[str, str], bytes]:
    """POST url-encoded form data that may repeat a key (e.g. several `file_ids`) — the shared
    `request()` helper's `form` parameter is a plain dict and cannot express that."""
    return await request(app.app, "POST", path, body=urlencode(pairs).encode())


def _review_tokens(page: bytes) -> dict[str, str]:
    return {
        m.group(1).decode(): m.group(2).decode()
        for m in re.finditer(rb'name="review_tokens" value="([^|]+)\|([^"]*)"', page)
    }


def _control_tokens(page: bytes) -> dict[str, str]:
    return {
        m.group(1).decode(): m.group(2).decode()
        for m in re.finditer(rb'name="control_tokens" value="([^|]+)\|([^"]*)"', page)
    }


def _governing_tokens(page: bytes) -> dict[str, str]:
    return {
        m.group(1).decode(): m.group(2).decode()
        for m in re.finditer(rb'name="governing_tokens" value="([^|]+)\|([^"]*)"', page)
    }


def _make_bid(bid_id: str, project_name: str, now: datetime) -> Bid:
    return Bid(
        bid_id=bid_id,
        customer="Synthetic EPC",
        customer_type=CustomerType.EPC,
        project_name=project_name,
        sales_owner="Sales",
        bc_owner="Jason",
        release_date=date(2026, 9, 1),
        customer_due_date=date(2026, 10, 1),
        internal_due_date=date(2026, 9, 25),
        estimated_value=Decimal("1000"),
        classification=BidLevel.LEVEL_0,
        current_gate=Gate.G0,
        status=BidStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def ui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    source = tmp_path / "intake-inbox"
    initial = source / "Test2-Bid"
    initial.mkdir(parents=True)
    for index in range(1, 17):
        # .txt, not .pdf: OPS-13A extraction only supports pdf/docx/xlsx and cleanly skips
        # anything else (`UnsupportedMediaTypeError`, caught and continued) — this test is
        # about the review/document-control/Bid-Basis/discovery-*gate*, not extraction quality
        # (already covered by `test_deliverable_discovery_extraction.py`), and a real (non-fake)
        # .pdf is unnecessary complexity for that.
        (initial / f"f{index:02d}.txt").write_bytes(f"synthetic file {index}".encode())
    addendum = source / "Addendum-01"
    addendum.mkdir()
    for index in range(1, 4):
        (addendum / f"a{index:02d}.txt").write_bytes(f"synthetic addendum {index}".encode())

    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setenv("CONTRACTIQ_DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setenv("CONTRACTIQ_PROPOSAL_ARTIFACT_ROOT", str(tmp_path / "proposal"))
    monkeypatch.setenv("CONTRACTIQ_INTAKE_STORAGE_ROOT", str(tmp_path / "intake"))
    monkeypatch.delenv("CONTRACTIQ_INTAKE_SOURCE_ROOTS", raising=False)
    monkeypatch.setenv("CONTRACTIQ_INTAKE_ROOTS", str(source))
    monkeypatch.setenv("CONTRACTIQ_DISCOVERY_RUNNER", "off")
    previous = sys.modules.pop("app", None)
    import app

    app.deliverable_discovery_service.llm_client = _StubLLM()
    app.discovery_job_runner.llm_client = _StubLLM()
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    app.bid_repository.create_bid(_make_bid(BID_ID, "Test2 Bid", now))
    yield app
    sys.modules.pop("app", None)
    if previous is not None:
        sys.modules["app"] = previous


async def _register_release(
    app: ModuleType,
    *,
    folder: str,
    release_type: str,
    reference: str,
    mode: str,
) -> str:
    path = f"/bids/{BID_ID}/package-intake-addenda"
    status, _, preview = await request(
        app.app,
        "POST",
        f"{path}/preview",
        {
            "release_type": release_type,
            "source_root_alias": "inbox_1",
            "source_folder": folder,
            "intake_mode": mode,
            "exact_customer_reference": reference,
            "customer_issue_date": "2026-09-11",
            "received_channel": "EMAIL",
        },
    )
    assert status == 200, preview[:400]
    token = re.search(rb'name="confirmation_token" value="([^"]+)"', preview)
    assert token is not None
    status, headers, _ = await request(
        app.app, "POST", f"{path}/register", {"confirmation_token": token.group(1).decode()}
    )
    assert status == 303
    location = urlsplit(headers["location"])
    assert parse_qs(location.query) == {
        "notice": ["received"],
        "count": ["16" if folder == "Test2-Bid" else "3"],
    }
    assert location.fragment == "files"
    return location.path


def test_16_file_package_bulk_review_then_bulk_document_control_reaches_discovery(
    ui_app: ModuleType,
) -> None:
    async def scenario() -> None:
        release_path = await _register_release(
            ui_app,
            folder="Test2-Bid",
            release_type="INITIAL_PACKAGE",
            reference="Test2 Bid initial package",
            mode="initial",
        )
        status, _, page = await request(ui_app.app, "GET", release_path)
        assert status == 200
        tokens = _review_tokens(page)
        assert len(tokens) == 16
        file_ids = sorted(tokens)  # f01..f16, sorted lexicographically == numeric order

        first_thirteen = file_ids[:13]
        last_three = file_ids[13:]

        # Simulate Jason's prior review: the first 13 already marked "Include".
        pairs: list[tuple[str, str]] = [("operation_id", "bulk-review-1")]
        pairs += [("analysis_eligibility", "ELIGIBLE")]
        for file_id in first_thirteen:
            pairs.append(("file_ids", file_id))
            pairs.append(("review_tokens", f"{file_id}|{tokens[file_id]}"))
        status, headers, _ = await _post_form(ui_app, f"{release_path}/bulk-dispositions", pairs)
        assert status == 303, headers

        status, _, page = await request(ui_app.app, "GET", release_path)
        assert status == 200
        assert b"3 of 16 received file(s) still need a review decision" in page
        assert b"No file has been selected as a governing Bid Basis source yet" in page
        assert b"still need an exact controlled version" not in page
        assert b"This package is ready" not in page
        # the previous, misleading wording must be gone
        assert b"Use 'Put under document control' on each one" not in page
        assert b"Still to do on this package" not in page

        # "Bulk review the last three."
        tokens = _review_tokens(page)
        pairs = [("operation_id", "bulk-review-2"), ("analysis_eligibility", "ELIGIBLE")]
        for file_id in last_three:
            pairs.append(("file_ids", file_id))
            pairs.append(("review_tokens", f"{file_id}|{tokens[file_id]}"))
        status, headers, _ = await _post_form(ui_app, f"{release_path}/bulk-dispositions", pairs)
        assert status == 303, headers

        status, _, page = await request(ui_app.app, "GET", release_path)
        assert status == 200
        # Review is truthfully reported as complete...
        assert b"All received files have been reviewed" in page
        assert b"Find required Bid submissions" in page
        # ...independently of governing-source selection and document control.
        assert b"No file has been selected as a governing Bid Basis source yet" in page
        assert b"This package is ready" not in page

        governing_tokens = _governing_tokens(page)
        assert len(governing_tokens) == 16
        pairs = [
            ("operation_id", "bulk-governing-1"),
            ("governing_disposition", "GOVERNING"),
        ]
        for file_id, event_id in governing_tokens.items():
            pairs.append(("file_ids", file_id))
            pairs.append(("governing_tokens", f"{file_id}|{event_id}"))
        status, headers, _ = await _post_form(
            ui_app, f"{release_path}/bulk-governing-sources", pairs
        )
        assert status == 303, headers

        status, _, page = await request(ui_app.app, "GET", release_path)
        assert status == 200
        assert (
            b"16 governing source file(s) selected; 16 still need exact controlled versions" in page
        )
        assert b"This package is ready" not in page

        control_tokens = _control_tokens(page)
        assert len(control_tokens) == 16

        # Bulk-control all 16 governing sources in one action, reusing managed originals.
        pairs = [("category", "SOLICITATION"), ("version_label", "Original")]
        for file_id, operation_id in control_tokens.items():
            pairs.append(("file_ids", file_id))
            pairs.append(("control_tokens", f"{file_id}|{operation_id}"))
        status, headers, body = await _post_form(
            ui_app, f"{release_path}/bulk-controlled-documents", pairs
        )
        assert status == 303, body[:800]

        status, _, page = await request(ui_app.app, "GET", release_path)
        assert status == 200
        assert b"This package is ready" in page
        assert b"confirm the current Bid basis" in page
        assert b"still need an exact controlled version" not in page

        # Each file became its own controlled document — no shared identity, no re-upload.
        entries = ui_app.document_service.list_register_entries(bid_id=BID_ID)
        assert len(entries) == 16
        document_ids = {entry.document.document_id for entry in entries}
        assert len(document_ids) == 16
        # Byte-exact: the controlled version's digest matches the imported synthetic source.
        by_title = {entry.document.title: entry for entry in entries}
        assert "f01" in by_title

        expected = hashlib.sha256(b"synthetic file 1").hexdigest()
        assert by_title["f01"].current_version.sha256_digest == expected

        # The real gate: the release is now genuinely incorporable.
        release_id = release_path.rsplit("/", 1)[-1]
        assert ui_app.bid_package_repository.incorporation_issues(BID_ID, release_id) == ()

        # Publish the Bid Basis snapshot — the real next step, now unblocked.
        path = f"/bids/{BID_ID}/package-intake-addenda"
        status, _, intake_page = await request(ui_app.app, "GET", path)
        assert status == 200
        assert b"Find required Bid submissions" in intake_page
        status, headers, body = await request(
            ui_app.app,
            "POST",
            f"{path}/basis-snapshots",
            {
                "operation_id": "basis-1",
                "expected_current_snapshot_id": "",
                "release_ids": release_id,
                "label": "Bid Basis 1",
            },
        )
        assert status == 303, body[:800]

        status, _, intake_page = await request(ui_app.app, "GET", path)
        assert status == 200
        assert b"Find required Bid submissions" in intake_page

        # OPS-13A discovery remains reachable after publishing the governing Bid Basis.
        _, discovery_page = await post_run_and_finish(
            ui_app,
            BID_ID,
            "run-1",
        )
        assert b"no published Bid Basis snapshot" not in discovery_page

    asyncio.run(scenario())


def test_addendum_package_uses_the_same_separated_status_and_bulk_control(
    ui_app: ModuleType,
) -> None:
    async def scenario() -> None:
        initial_path = await _register_release(
            ui_app,
            folder="Test2-Bid",
            release_type="INITIAL_PACKAGE",
            reference="Test2 Bid initial package",
            mode="initial",
        )
        status, _, page = await request(ui_app.app, "GET", initial_path)
        tokens = _review_tokens(page)
        pairs: list[tuple[str, str]] = [
            ("operation_id", "bulk-review-initial"),
            ("analysis_eligibility", "ELIGIBLE"),
        ]
        for file_id, event_id in tokens.items():
            pairs.append(("file_ids", file_id))
            pairs.append(("review_tokens", f"{file_id}|{event_id}"))
        status, headers, _ = await _post_form(ui_app, f"{initial_path}/bulk-dispositions", pairs)
        assert status == 303

        status, _, page = await request(ui_app.app, "GET", initial_path)
        governing_tokens = _governing_tokens(page)
        pairs = [
            ("operation_id", "bulk-governing-initial"),
            ("governing_disposition", "GOVERNING"),
        ]
        for file_id, event_id in governing_tokens.items():
            pairs.append(("file_ids", file_id))
            pairs.append(("governing_tokens", f"{file_id}|{event_id}"))
        status, headers, _ = await _post_form(
            ui_app, f"{initial_path}/bulk-governing-sources", pairs
        )
        assert status == 303

        status, _, page = await request(ui_app.app, "GET", initial_path)
        control_tokens = _control_tokens(page)
        pairs = [("category", "SOLICITATION"), ("version_label", "Original")]
        for file_id, operation_id in control_tokens.items():
            pairs.append(("file_ids", file_id))
            pairs.append(("control_tokens", f"{file_id}|{operation_id}"))
        status, headers, body = await _post_form(
            ui_app, f"{initial_path}/bulk-controlled-documents", pairs
        )
        assert status == 303, body[:800]

        initial_release_id = initial_path.rsplit("/", 1)[-1]
        path = f"/bids/{BID_ID}/package-intake-addenda"
        status, headers, body = await request(
            ui_app.app,
            "POST",
            f"{path}/basis-snapshots",
            {
                "operation_id": "basis-initial",
                "expected_current_snapshot_id": "",
                "release_ids": initial_release_id,
                "label": "Bid Basis 1",
            },
        )
        assert status == 303, body[:800]
        current_snapshot_id = ui_app.bid_package_repository.current_snapshot(BID_ID)["snapshot_id"]

        addendum_path = await _register_release(
            ui_app,
            folder="Addendum-01",
            release_type="ADDENDUM",
            reference="Addendum 1",
            mode="addendum",
        )
        status, _, page = await request(ui_app.app, "GET", addendum_path)
        assert status == 200
        assert b"3 of 3 received file(s) still need a review decision" in page

        tokens = _review_tokens(page)
        pairs = [("operation_id", "bulk-review-addendum"), ("analysis_eligibility", "ELIGIBLE")]
        for file_id, event_id in tokens.items():
            pairs.append(("file_ids", file_id))
            pairs.append(("review_tokens", f"{file_id}|{event_id}"))
        status, headers, _ = await _post_form(ui_app, f"{addendum_path}/bulk-dispositions", pairs)
        assert status == 303

        status, _, page = await request(ui_app.app, "GET", addendum_path)
        assert b"All received files have been reviewed" in page
        assert b"No file has been selected as a governing Bid Basis source yet" in page

        governing_tokens = _governing_tokens(page)
        pairs = [
            ("operation_id", "bulk-governing-addendum"),
            ("governing_disposition", "GOVERNING"),
        ]
        for file_id, event_id in governing_tokens.items():
            pairs.append(("file_ids", file_id))
            pairs.append(("governing_tokens", f"{file_id}|{event_id}"))
        status, headers, _ = await _post_form(
            ui_app, f"{addendum_path}/bulk-governing-sources", pairs
        )
        assert status == 303

        status, _, page = await request(ui_app.app, "GET", addendum_path)
        assert (
            b"3 governing source file(s) selected; 3 still need exact controlled versions" in page
        )

        control_tokens = _control_tokens(page)
        assert len(control_tokens) == 3
        pairs = [("category", "ADDENDUM"), ("version_label", "Original")]
        for file_id, operation_id in control_tokens.items():
            pairs.append(("file_ids", file_id))
            pairs.append(("control_tokens", f"{file_id}|{operation_id}"))
        status, headers, body = await _post_form(
            ui_app, f"{addendum_path}/bulk-controlled-documents", pairs
        )
        assert status == 303, body[:800]

        status, _, page = await request(ui_app.app, "GET", addendum_path)
        assert b"This package is ready" in page

        addendum_release_id = addendum_path.rsplit("/", 1)[-1]
        status, headers, body = await _post_form(
            ui_app,
            f"{path}/basis-snapshots",
            [
                ("operation_id", "basis-updated"),
                ("expected_current_snapshot_id", current_snapshot_id),
                ("release_ids", initial_release_id),
                ("release_ids", addendum_release_id),
                ("label", "Bid Basis 2"),
            ],
        )
        assert status == 303, body[:800]
        updated_snapshot_id = ui_app.bid_package_repository.current_snapshot(BID_ID)["snapshot_id"]
        incorporated = ui_app.bid_package_repository.snapshot_release_ids(updated_snapshot_id)
        assert set(incorporated) == {initial_release_id, addendum_release_id}

    asyncio.run(scenario())
