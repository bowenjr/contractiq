from __future__ import annotations

import asyncio
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

BID_ID = "B-2026-1111"


@pytest.fixture
def ui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    source = tmp_path / "intake-inbox"
    source.mkdir()
    initial = source / "Initial-Package"
    (initial / "folder").mkdir(parents=True)
    (initial / "folder" / "synthetic.pdf").write_bytes(b"%PDF synthetic OPS-11")
    addendum = source / "Addendum-01"
    addendum.mkdir()
    (addendum / "change.txt").write_text("synthetic addendum")
    outside = tmp_path / "outside"
    outside.mkdir()
    (source / "unsafe-link").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setenv("CONTRACTIQ_DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setenv("CONTRACTIQ_PROPOSAL_ARTIFACT_ROOT", str(tmp_path / "proposal"))
    monkeypatch.setenv("CONTRACTIQ_INTAKE_STORAGE_ROOT", str(tmp_path / "intake"))
    monkeypatch.delenv("CONTRACTIQ_INTAKE_SOURCE_ROOTS", raising=False)
    monkeypatch.setenv("CONTRACTIQ_INTAKE_ROOTS", str(source))
    previous = sys.modules.pop("app", None)
    import app

    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    app.bid_repository.create_bid(
        Bid(
            bid_id=BID_ID,
            customer="Synthetic EPC",
            customer_type=CustomerType.EPC,
            project_name="Synthetic Browser Bid",
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
    )
    yield app
    sys.modules.pop("app", None)
    if previous is not None:
        sys.modules["app"] = previous


def test_seventh_workspace_preview_register_review_and_no_get_mutation(
    ui_app: ModuleType,
) -> None:
    async def scenario() -> None:
        path = f"/bids/{BID_ID}/package-intake-addenda"
        audit_before = len(ui_app.bid_repository.list_audit(BID_ID))
        status, _, page = await request(ui_app.app, "GET", path)
        assert status == 200
        assert b"Package intake and addenda" in page
        assert b"Import customer bid package" in page
        assert b"Record Portal / Email Check" not in page
        assert b"Prepare Work AI Package" not in page
        assert len(ui_app.bid_repository.list_audit(BID_ID)) == audit_before

        status, _, inbox = await request(
            ui_app.app,
            "GET",
            f"{path}/import?mode=initial",
        )
        assert status == 200
        assert b"Initial-Package" in inbox and b"Addendum-01" in inbox
        assert b"unsafe-link" not in inbox
        assert str(ui_app.INTAKE_SOURCE_ROOTS["inbox_1"]).encode() not in inbox
        assert len(ui_app.bid_repository.list_audit(BID_ID)) == audit_before

        status, _, rejected = await request(
            ui_app.app,
            "POST",
            f"{path}/preview",
            {
                "source_root_alias": "inbox_1",
                "source_folder": "../outside",
                "received_channel": "EMAIL",
            },
        )
        assert status == 422 and b"Package intake needs attention" in rejected
        assert len(ui_app.bid_repository.list_audit(BID_ID)) == audit_before

        status, _, selected = await request(
            ui_app.app,
            "GET",
            f"{path}/import?mode=initial&source_root_alias=inbox_1&source_folder=Initial-Package",
        )
        assert status == 200 and b"Selected folder: Initial-Package" in selected
        assert len(ui_app.bid_repository.list_audit(BID_ID)) == audit_before

        status, _, preview = await request(
            ui_app.app,
            "POST",
            f"{path}/preview",
            {
                "release_type": "INITIAL_PACKAGE",
                "source_root_alias": "inbox_1",
                "source_folder": "Initial-Package",
                "intake_mode": "initial",
                "exact_customer_reference": "RFP-SYN-1",
                "customer_issue_date": "2026-09-11",
                "received_channel": "EMAIL",
                "note": "Synthetic browser receipt",
            },
        )
        assert status == 200
        assert b"Preview: Initial-Package" in preview and b"folder/synthetic.pdf" in preview
        assert len(ui_app.bid_repository.list_audit(BID_ID)) == audit_before
        token_match = re.search(rb'name="confirmation_token" value="([^"]+)"', preview)
        assert token_match is not None
        status, headers, _ = await request(
            ui_app.app,
            "POST",
            f"{path}/register",
            {"confirmation_token": token_match.group(1).decode()},
        )
        assert status == 303
        release_path = headers["location"]
        status, _, detail = await request(ui_app.app, "GET", release_path)
        assert status == 200
        assert b"folder/synthetic.pdf" in detail
        # The receipt time is recorded by the server, not typed, and never asked for.
        assert b'name="received_at"' not in detail
        registered = ui_app.bid_package_repository.summary(BID_ID).releases[0]
        assert str(registered["received_at"]).startswith("20")
        # Review state reads as business language, and no record identifier reaches the reader.
        assert b"Not yet decided" in detail and b"Not Assessed" not in detail
        assert b"Review selected files" in detail
        assert b"Select all files in this folder" in detail
        assert not re.search(rb"file RF-[0-9a-f]{8}", detail.split(b"Advanced:")[0])
        # An initial package cannot carry addendum directives, and acknowledgement is not routine.
        assert b"What does this addendum change?" not in detail
        assert b"Record acknowledgement event" not in detail
        # Confidence belongs to an automated proposal; a safe default has none to offer.
        assert b"Confidence" not in detail
        audit_after_register = len(ui_app.bid_repository.list_audit(BID_ID))
        assert audit_after_register == audit_before + 1
        assert ui_app.bid_package_repository.summary(BID_ID).notices == ()
        status, _, imported_inbox = await request(
            ui_app.app,
            "GET",
            f"{path}/import?mode=addendum",
        )
        assert (
            status == 200
            and b"Already imported" in imported_inbox
            and b"Addendum-01" in imported_inbox
        )
        status, _, _ = await request(ui_app.app, "GET", release_path)
        assert status == 200
        assert len(ui_app.bid_repository.list_audit(BID_ID)) == audit_after_register

        release = ui_app.bid_package_repository.summary(BID_ID).releases[0]
        release_id = str(release["release_id"])
        file_id = str(
            ui_app.bid_package_repository.release_detail(BID_ID, release_id)["files"][0]["file_id"]
        )
        status, _, empty_bulk = await request(
            ui_app.app,
            "POST",
            f"{path}/releases/{release_id}/bulk-dispositions",
            {"analysis_eligibility": "ELIGIBLE", "operation_id": "empty-bulk"},
        )
        assert status == 422
        assert b"Package intake needs attention" in empty_bulk
        assert b"Review selected files" in empty_bulk
        assert len(ui_app.bid_repository.list_audit(BID_ID)) == audit_after_register
        status, _, retained = await request(
            ui_app.app,
            "POST",
            f"{path}/files/{file_id}/dispositions",
            {
                "release_id": release_id,
                "content_form": "DRAWING",
                "analysis_eligibility": "EXCLUDED",
                "exclusion_reason": "",
                "operation_id": "invalid-disposition",
            },
        )
        assert status == 422
        assert b"excluded files require an exclusion reason" in retained
        assert b"Drawing" in retained and b"folder/synthetic.pdf" in retained
        assert len(ui_app.bid_repository.list_audit(BID_ID)) == audit_after_register

        status, _, addendum_preview = await request(
            ui_app.app,
            "POST",
            f"{path}/preview",
            {
                "release_type": "ADDENDUM",
                "source_root_alias": "inbox_1",
                "source_folder": "Addendum-01",
                "intake_mode": "addendum",
                "exact_customer_reference": "ADD-01",
                "received_at": "2026-09-12T09:00",
                "received_channel": "EMAIL",
            },
        )
        assert status == 200 and b"Preview: Addendum-01" in addendum_preview
        token_match = re.search(rb'name="confirmation_token" value="([^"]+)"', addendum_preview)
        assert token_match is not None
        status, _, _ = await request(
            ui_app.app,
            "POST",
            f"{path}/register",
            {"confirmation_token": token_match.group(1).decode()},
        )
        assert status == 303
        assert len(ui_app.bid_package_repository.summary(BID_ID).releases) == 2

    asyncio.run(scenario())


def test_cross_bid_download_is_denied_and_source_paths_are_not_exposed(
    ui_app: ModuleType,
) -> None:
    async def scenario() -> None:
        path = f"/bids/{BID_ID}/package-intake-addenda"
        status, _, preview = await request(
            ui_app.app,
            "POST",
            f"{path}/preview",
            {
                "release_type": "INITIAL_PACKAGE",
                "source_root_alias": "inbox_1",
                "source_folder": "Initial-Package",
                "intake_mode": "initial",
                "exact_customer_reference": "RFP-SYN-2",
                "received_channel": "PORTAL",
            },
        )
        assert status == 200
        token = re.search(rb'name="confirmation_token" value="([^"]+)"', preview)
        assert token is not None
        status, headers, _ = await request(
            ui_app.app,
            "POST",
            f"{path}/register",
            {"confirmation_token": token.group(1).decode()},
        )
        assert status == 303
        release = ui_app.bid_package_repository.summary(BID_ID).releases[0]
        release_id = str(release["release_id"])
        file_id = str(
            ui_app.bid_package_repository.release_detail(BID_ID, release_id)["files"][0]["file_id"]
        )
        status, _, body = await request(
            ui_app.app,
            "GET",
            f"/bids/B-2026-9999/package-intake-addenda/releases/{release_id}/files/{file_id}/download",
        )
        assert status == 404
        status, _, page = await request(ui_app.app, "GET", path)
        assert status == 200
        assert str(ui_app.INTAKE_SOURCE_ROOTS["inbox_1"]).encode() not in page
        assert str(ui_app.MANAGED_INTAKE_ROOT).encode() not in page

    asyncio.run(scenario())


def test_bid_creation_applies_minimum_or_allows_only_stricter_governance(
    ui_app: ModuleType,
) -> None:
    async def scenario() -> None:
        base = {
            "customer": "Synthetic EPC",
            "customer_type": "epc",
            "sales_owner": "Sales",
            "bc_owner": "Jason",
            "release_date": "2026-09-01",
            "customer_due_date": "2026-10-01",
            "internal_due_date": "2026-09-25",
            "estimated_value": "1000",
            "currency": "CAD",
        }
        status, headers, _ = await request(
            ui_app.app,
            "POST",
            "/bids",
            {**base, "project_name": "Automatic governance"},
        )
        assert status == 303
        automatic_id = headers["location"].removeprefix("/bids/")
        automatic = ui_app.bid_repository.get_bid(automatic_id)
        assert automatic is not None and automatic.classification is BidLevel.LEVEL_3
        assessment = ui_app.ops07w_repository.latest_assessment(automatic_id)
        assert assessment is not None
        assert assessment.recommended_level is BidLevel.LEVEL_3
        assert assessment.selected_level is BidLevel.LEVEL_3

        status, headers, _ = await request(
            ui_app.app,
            "POST",
            "/bids",
            {
                **base,
                "project_name": "Stricter governance",
                "classification": "level_4",
                "classification_override_rationale": "Executive review requested",
            },
        )
        assert status == 303
        stricter = ui_app.bid_repository.get_bid(headers["location"].removeprefix("/bids/"))
        assert stricter is not None and stricter.classification is BidLevel.LEVEL_4

        status, _, retained = await request(
            ui_app.app,
            "POST",
            "/bids",
            {
                **base,
                "project_name": "Rejected lower governance",
                "classification": "level_0",
            },
        )
        assert status == 422
        assert b"cannot be lower than the recommended minimum" in retained
        assert b"Rejected lower governance" in retained

    asyncio.run(scenario())


def test_a_received_file_goes_under_document_control_without_a_second_upload(
    ui_app: ModuleType,
) -> None:
    """The managed original ContractIQ holds becomes the controlled document's first bytes.

    This is what makes the recorded exact-bytes relationship provable rather than asserted, so the
    test compares the stored document digest against the received-file digest.
    """

    async def scenario() -> None:
        path = f"/bids/{BID_ID}/package-intake-addenda"
        status, _, selected = await request(
            ui_app.app,
            "GET",
            f"{path}/import?mode=initial&source_root_alias=inbox_1&source_folder=Initial-Package",
        )
        assert status == 200
        status, _, preview = await request(
            ui_app.app,
            "POST",
            f"{path}/preview",
            {
                "release_type": "INITIAL_PACKAGE",
                "source_root_alias": "inbox_1",
                "source_folder": "Initial-Package",
                "intake_mode": "initial",
                "exact_customer_reference": "RFP-SYN-2",
                "received_channel": "EMAIL",
            },
        )
        assert status == 200
        token = re.search(rb'name="confirmation_token" value="([^"]+)"', preview)
        assert token is not None
        status, headers, _ = await request(
            ui_app.app, "POST", f"{path}/register", {"confirmation_token": token.group(1).decode()}
        )
        assert status == 303
        release_path = headers["location"]
        release_id = release_path.rsplit("/", 1)[-1]
        detail = ui_app.bid_package_repository.release_detail(BID_ID, release_id)
        received = detail["files"][0]
        file_id = str(received["file_id"])

        # The form proposes a title from the filename and asks for no file.
        status, _, page = await request(ui_app.app, "GET", release_path)
        assert status == 200
        assert b"You do not need to find or upload this file again" in page
        assert b'type="file"' not in page

        documents_before = len(ui_app.document_service.list_register_entries(bid_id=BID_ID))
        status, headers, body = await request(
            ui_app.app,
            "POST",
            f"{path}/files/{file_id}/controlled-document",
            {
                "release_id": release_id,
                "operation_id": "6f1d6d1e-0a2a-4c2b-9c3f-9a1e2b3c4d5e",
                "title": "Synthetic RFP letter",
                "category": "SOLICITATION",
                "version_label": "Original",
            },
        )
        assert status == 303, body[:400]

        entries = ui_app.document_service.list_register_entries(bid_id=BID_ID)
        assert len(entries) == documents_before + 1
        created = next(entry for entry in entries if entry.document.title == "Synthetic RFP letter")
        # The controlled bytes are the received bytes, not a re-upload that merely claims to be.
        assert created.current_version.sha256_digest == received["sha256"]
        assert created.current_version.byte_size == received["byte_size"]

        # The link is recorded automatically as exact bytes; the user chose no relationship.
        linked = ui_app.bid_package_repository.release_detail(BID_ID, release_id)["files"][0]
        assert linked["document_version_id"] == created.current_version.document_version_id
        assert linked["document_id"] == created.document.document_id

        # Repeating the same operation identity must not create a second document.
        status, _, _ = await request(
            ui_app.app,
            "POST",
            f"{path}/files/{file_id}/controlled-document",
            {
                "release_id": release_id,
                "operation_id": "6f1d6d1e-0a2a-4c2b-9c3f-9a1e2b3c4d5e",
                "title": "Synthetic RFP letter",
                "category": "SOLICITATION",
                "version_label": "Original",
            },
        )
        assert status == 303
        assert len(ui_app.document_service.list_register_entries(bid_id=BID_ID)) == (
            documents_before + 1
        )

        # Once under control the page offers the document, not another registration form.
        status, _, after = await request(ui_app.app, "GET", release_path)
        assert status == 200
        assert b"Open the controlled document" in after

    asyncio.run(scenario())


def test_an_addendum_offers_directives_and_an_initial_package_does_not(
    ui_app: ModuleType,
) -> None:
    async def scenario() -> None:
        path = f"/bids/{BID_ID}/package-intake-addenda"

        async def ingest(folder: str, release_type: str, reference: str) -> str:
            status, _, preview = await request(
                ui_app.app,
                "POST",
                f"{path}/preview",
                {
                    "release_type": release_type,
                    "source_root_alias": "inbox_1",
                    "source_folder": folder,
                    "intake_mode": "initial" if release_type == "INITIAL_PACKAGE" else "addendum",
                    "exact_customer_reference": reference,
                    "received_channel": "EMAIL",
                },
            )
            assert status == 200, preview[:300]
            token = re.search(rb'name="confirmation_token" value="([^"]+)"', preview)
            assert token is not None
            status, headers, _ = await request(
                ui_app.app,
                "POST",
                f"{path}/register",
                {"confirmation_token": token.group(1).decode()},
            )
            assert status == 303
            return headers["location"]

        initial_path = await ingest("Initial-Package", "INITIAL_PACKAGE", "RFP-SYN-3")
        status, _, initial = await request(ui_app.app, "GET", initial_path)
        assert status == 200
        assert b"What does this addendum change?" not in initial
        assert b"Record acknowledgement event" not in initial

        addendum_path = await ingest("Addendum-01", "ADDENDUM", "Addendum 01")
        status, _, addendum = await request(ui_app.app, "GET", addendum_path)
        assert status == 200
        assert b"What does this addendum change?" in addendum
        # The directive vocabulary is the customer's effect, not an internal code.
        assert b"Replaces earlier document" in addendum
        assert b"Withdraws earlier document" in addendum
        assert b'placeholder="CHANGE, ADD, WITHDRAW, CLARIFY"' not in addendum
        # Acknowledgement stays out of the routine path until an event already exists.
        assert b"Record acknowledgement event" not in addendum

    asyncio.run(scenario())
