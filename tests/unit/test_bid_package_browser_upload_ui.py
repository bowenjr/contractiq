"""ASGI-level acceptance for the browser folder-picker intake route: the actual HTTP
endpoints and rendered pages, not just the service/storage layer already covered by
`test_bid_package_browser_upload.py`. Mirrors the `ui_app` fixture and `request()` helper
pattern in `test_ops11_ui.py`. No real customer files — every upload is synthetic bytes
built at test time."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from core.enums import BidLevel, BidStatus, CustomerType, Gate
from core.schemas import Bid
from scripts.asgi_acceptance_ops08 import request

BID_ID = "B-2026-2222"
OTHER_BID_ID = "B-2026-3333"


def _multipart_upload(fields: dict[str, str], files: list[tuple[str, bytes]]) -> tuple[bytes, str]:
    """Build a multipart/form-data body with several `files` parts, each carrying its own
    relative-path filename — the same shape the browser JS builds from `webkitRelativePath`."""
    boundary = "contractiq-browser-upload-boundary"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )
    for relative_path, content in files:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="files"; filename="',
                relative_path.encode(),
                b'"\r\nContent-Type: application/octet-stream\r\n\r\n',
                content,
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


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
    source.mkdir()
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setenv("CONTRACTIQ_DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setenv("CONTRACTIQ_PROPOSAL_ARTIFACT_ROOT", str(tmp_path / "proposal"))
    monkeypatch.setenv("CONTRACTIQ_INTAKE_STORAGE_ROOT", str(tmp_path / "intake"))
    monkeypatch.delenv("CONTRACTIQ_INTAKE_SOURCE_ROOTS", raising=False)
    monkeypatch.setenv("CONTRACTIQ_INTAKE_ROOTS", str(source))
    previous = sys.modules.pop("app", None)
    import app

    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    app.bid_repository.create_bid(_make_bid(BID_ID, "Test2 Bid", now))
    app.bid_repository.create_bid(_make_bid(OTHER_BID_ID, "Other Bid", now))
    yield app
    sys.modules.pop("app", None)
    if previous is not None:
        sys.modules["app"] = previous


def test_browser_folder_picker_is_primary_and_advanced_inbox_is_collapsed(
    ui_app: ModuleType,
) -> None:
    async def scenario() -> None:
        path = f"/bids/{BID_ID}/package-intake-addenda/import?mode=initial"
        status, _, page = await request(ui_app.app, "GET", path)
        assert status == 200
        assert b"Choose folder from this computer" in page
        assert b"webkitdirectory" in page
        assert page.index(b"Choose folder from this computer") < page.index(
            b"configured server inbox"
        )
        assert b"<details" in page

    asyncio.run(scenario())


def test_full_browser_upload_journey_through_the_actual_routes(ui_app: ModuleType) -> None:
    async def scenario() -> None:
        base = f"/bids/{BID_ID}/package-intake-addenda"
        audit_before = len(ui_app.bid_repository.list_audit(BID_ID))

        body, content_type = _multipart_upload(
            {"mode": "initial"},
            [
                ("Test2 Bid/RFP.pdf", b"%PDF fake rfp content"),
                ("Test2 Bid/Attachments/Drawing.pdf", b"%PDF fake nested drawing"),
            ],
        )
        status, _, response = await request(
            ui_app.app,
            "POST",
            f"{base}/browser-upload",
            body=body,
            content_type=content_type,
        )
        assert status == 200, response
        payload = json.loads(response)
        redirect_url = payload["redirect_url"]
        assert "source_root_alias=browser" in redirect_url
        # staging only — no release, Bid Basis change, or audit row yet
        assert len(ui_app.bid_repository.list_audit(BID_ID)) == audit_before

        status, _, selected = await request(ui_app.app, "GET", redirect_url)
        assert status == 200
        assert b"Selected folder: Test2 Bid" in selected
        assert b"2 file(s)" in selected
        assert len(ui_app.bid_repository.list_audit(BID_ID)) == audit_before

        token = re.search(r"source_folder=([0-9a-f]{32})", redirect_url)
        assert token is not None
        upload_token = token.group(1)

        status, _, preview = await request(
            ui_app.app,
            "POST",
            f"{base}/preview",
            {
                "release_type": "INITIAL_PACKAGE",
                "source_root_alias": "browser",
                "source_folder": upload_token,
                "intake_mode": "initial",
                "exact_customer_reference": "Test2 Bid initial package",
                "customer_issue_date": "2026-09-11",
                "received_channel": "EMAIL",
            },
        )
        assert status == 200, preview[:400]
        assert b"RFP.pdf" in preview and b"Attachments/Drawing.pdf" in preview
        confirmation = re.search(rb'name="confirmation_token" value="([^"]+)"', preview)
        assert confirmation is not None
        assert len(ui_app.bid_repository.list_audit(BID_ID)) == audit_before

        status, headers, _ = await request(
            ui_app.app,
            "POST",
            f"{base}/register",
            {"confirmation_token": confirmation.group(1).decode()},
        )
        assert status == 303
        release_path = headers["location"]
        assert len(ui_app.bid_repository.list_audit(BID_ID)) == audit_before + 1

        status, _, detail = await request(ui_app.app, "GET", release_path)
        assert status == 200
        assert b"Continue to controlled customer documents" in detail
        # the next step lands on a visible control on this page, not the generic upload register
        assert b'aria-label="Continue to controlled customer documents"' in detail
        assert f"/documents?bid_id={BID_ID}".encode() not in detail

        # the ephemeral staging directory was discarded once its bytes were durably staged
        storage = ui_app.bid_package_storage
        assert not (storage.browser_uploads_root / upload_token).exists()

    asyncio.run(scenario())


def test_addendum_mode_reuses_the_browser_upload_route(ui_app: ModuleType) -> None:
    async def scenario() -> None:
        base = f"/bids/{BID_ID}/package-intake-addenda"
        body, content_type = _multipart_upload(
            {"mode": "addendum"},
            [("Addendum 1/Revised-RFP.pdf", b"revised content")],
        )
        status, _, response = await request(
            ui_app.app, "POST", f"{base}/browser-upload", body=body, content_type=content_type
        )
        assert status == 200
        redirect_url = json.loads(response)["redirect_url"]
        assert "mode=addendum" in redirect_url

        status, _, selected = await request(ui_app.app, "GET", redirect_url)
        assert status == 200
        assert b"Selected folder: Addendum 1" in selected

    asyncio.run(scenario())


def test_malformed_relative_path_is_rejected_with_a_readable_error(ui_app: ModuleType) -> None:
    async def scenario() -> None:
        base = f"/bids/{BID_ID}/package-intake-addenda"
        for bad_path in ("../etc/passwd", "/etc/passwd", "Test2 Bid/../../evil"):
            body, content_type = _multipart_upload({"mode": "initial"}, [(bad_path, b"malicious")])
            status, _, response = await request(
                ui_app.app,
                "POST",
                f"{base}/browser-upload",
                body=body,
                content_type=content_type,
            )
            assert status == 422, response
            assert "error" in json.loads(response)

    asyncio.run(scenario())


def test_duplicate_upload_submission_does_not_duplicate_the_release(ui_app: ModuleType) -> None:
    async def scenario() -> None:
        base = f"/bids/{BID_ID}/package-intake-addenda"
        body, content_type = _multipart_upload(
            {"mode": "initial"}, [("Test2 Bid/a.pdf", b"content-a")]
        )
        status, _, response = await request(
            ui_app.app, "POST", f"{base}/browser-upload", body=body, content_type=content_type
        )
        assert status == 200
        redirect_url = json.loads(response)["redirect_url"]
        token = re.search(r"source_folder=([0-9a-f]{32})", redirect_url).group(1)  # type: ignore[union-attr]

        status, _, preview = await request(
            ui_app.app,
            "POST",
            f"{base}/preview",
            {
                "release_type": "INITIAL_PACKAGE",
                "source_root_alias": "browser",
                "source_folder": token,
                "intake_mode": "initial",
                "exact_customer_reference": "Test2 Bid",
                "customer_issue_date": "2026-09-11",
                "received_channel": "EMAIL",
            },
        )
        confirmation = re.search(rb'name="confirmation_token" value="([^"]+)"', preview)
        assert confirmation is not None
        form = {"confirmation_token": confirmation.group(1).decode()}
        status_first, headers_first, _ = await request(ui_app.app, "POST", f"{base}/register", form)
        status_second, headers_second, _ = await request(
            ui_app.app, "POST", f"{base}/register", form
        )
        assert status_first == status_second == 303
        assert headers_first["location"] == headers_second["location"]
        assert len(ui_app.bid_package_repository.summary(BID_ID).releases) == 1

    asyncio.run(scenario())


def test_cross_bid_staging_token_is_rejected_at_the_route_level(ui_app: ModuleType) -> None:
    async def scenario() -> None:
        base_a = f"/bids/{BID_ID}/package-intake-addenda"
        base_b = f"/bids/{OTHER_BID_ID}/package-intake-addenda"
        body, content_type = _multipart_upload(
            {"mode": "initial"}, [("Test2 Bid/a.pdf", b"content-a")]
        )
        status, _, response = await request(
            ui_app.app, "POST", f"{base_a}/browser-upload", body=body, content_type=content_type
        )
        assert status == 200
        redirect_url = json.loads(response)["redirect_url"]
        token = re.search(r"source_folder=([0-9a-f]{32})", redirect_url).group(1)  # type: ignore[union-attr]

        cross_bid_path = (
            f"{base_b}/import?mode=initial&source_root_alias=browser&source_folder={token}"
        )
        status, _, page = await request(ui_app.app, "GET", cross_bid_path)
        assert status == 422
        assert b"does not belong to this Bid" in page

        cross_bid_preview_status, _, cross_bid_preview = await request(
            ui_app.app,
            "POST",
            f"{base_b}/preview",
            {
                "release_type": "INITIAL_PACKAGE",
                "source_root_alias": "browser",
                "source_folder": token,
                "intake_mode": "initial",
                "exact_customer_reference": "Stolen token attempt",
                "customer_issue_date": "2026-09-11",
                "received_channel": "EMAIL",
            },
        )
        assert cross_bid_preview_status == 422
        assert b"does not belong to this Bid" in cross_bid_preview
        assert len(ui_app.bid_package_repository.summary(OTHER_BID_ID).releases) == 0

    asyncio.run(scenario())


def test_confirmation_failure_leaves_no_partial_release_or_audit(ui_app: ModuleType) -> None:
    async def scenario() -> None:
        base = f"/bids/{BID_ID}/package-intake-addenda"
        audit_before = len(ui_app.bid_repository.list_audit(BID_ID))
        body, content_type = _multipart_upload(
            {"mode": "initial"}, [("Test2 Bid/a.pdf", b"content-a")]
        )
        status, _, response = await request(
            ui_app.app, "POST", f"{base}/browser-upload", body=body, content_type=content_type
        )
        assert status == 200
        redirect_url = json.loads(response)["redirect_url"]
        token = re.search(r"source_folder=([0-9a-f]{32})", redirect_url).group(1)  # type: ignore[union-attr]

        status, _, preview = await request(
            ui_app.app,
            "POST",
            f"{base}/preview",
            {
                "release_type": "INITIAL_PACKAGE",
                "source_root_alias": "browser",
                "source_folder": token,
                "intake_mode": "initial",
                "exact_customer_reference": "Test2 Bid",
                "customer_issue_date": "2026-09-11",
                "received_channel": "EMAIL",
            },
        )
        confirmation = re.search(rb'name="confirmation_token" value="([^"]+)"', preview)
        assert confirmation is not None

        # tamper with the staged bytes between preview and confirm
        storage = ui_app.bid_package_storage
        tampered = storage.browser_uploads_root / token / "files" / "Test2 Bid" / "a.pdf"
        tampered.write_bytes(b"tampered-content")

        status, _, page = await request(
            ui_app.app,
            "POST",
            f"{base}/register",
            {"confirmation_token": confirmation.group(1).decode()},
        )
        assert status == 422
        assert b"source changed after preview" in page
        assert ui_app.bid_package_repository.summary(BID_ID).releases == ()
        assert len(ui_app.bid_repository.list_audit(BID_ID)) == audit_before
        # the upload itself is preserved so the user can retry confirmation
        assert (storage.browser_uploads_root / token).exists()

    asyncio.run(scenario())
