"""Socketless acceptance for OPS-11BY Bid package intake simplification.

Measures the routine intake workflow the way a bid manager meets it, and asserts the
simplifications hold: the server records receipt time, technical controls appear only when they
mean something, a received file reaches document control without a second upload, and no record
identifier is put in front of the reader.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import socket
import sys
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.enums import BidLevel, BidStatus, CustomerType, Gate  # noqa: E402
from core.schemas import Bid  # noqa: E402
from scripts.asgi_acceptance_ops08 import request  # noqa: E402

BID_ID = "B-2026-1212"
CONTROL_OPERATION = "1f2e3d4c-5b6a-4978-8695-0a1b2c3d4e5f"


def _seed(app_module: object) -> None:
    now = datetime(2026, 9, 15, 9, tzinfo=UTC)
    app_module.bid_repository.create_bid(  # type: ignore[attr-defined]
        Bid(
            bid_id=BID_ID,
            customer="Synthetic EPCM",
            customer_type=CustomerType.EPCM,
            project_name="OPS-11BY synthetic acceptance",
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


async def _ingest(app_module: object, path: str, folder: str, kind: str, reference: str) -> str:
    status, _, preview = await request(
        app_module.app,  # type: ignore[attr-defined]
        "POST",
        f"{path}/preview",
        {
            "release_type": kind,
            "source_root_alias": "inbox_1",
            "source_folder": folder,
            "intake_mode": "initial" if kind == "INITIAL_PACKAGE" else "addendum",
            "exact_customer_reference": reference,
            "received_channel": "EMAIL",
        },
    )
    assert status == 200, preview[:300]
    token = re.search(rb'name="confirmation_token" value="([^"]+)"', preview)
    assert token is not None, "preview did not offer a confirmation"
    status, headers, _ = await request(
        app_module.app,  # type: ignore[attr-defined]
        "POST",
        f"{path}/register",
        {"confirmation_token": token.group(1).decode()},
    )
    assert status == 303
    return str(headers["location"])


async def main() -> dict[str, int]:
    measurements = {
        "user_actions": 0,
        "page_departures": 0,
        "typed_fields": 0,
        "re_uploads_of_held_files": 0,
        "receipt_times_typed": 0,
        "record_identifiers_shown": 0,
        "get_mutations": 0,
        "source_mutations": 0,
    }
    with tempfile.TemporaryDirectory(prefix="contractiq-ops11by-") as directory:
        root = Path(directory)
        source = root / "intake-inbox"
        initial = source / "Initial-Package" / "Part A"
        initial.mkdir(parents=True)
        letter = initial / "Invitation-Letter.pdf"
        letter.write_bytes(b"%PDF synthetic OPS-11BY invitation")
        twin_a = source / "Initial-Package" / "Part B" / "Schedule.xlsx"
        twin_a.parent.mkdir(parents=True)
        twin_a.write_bytes(b"synthetic duplicate bytes")
        (source / "Initial-Package" / "Part B" / "Schedule-copy.xlsx").write_bytes(
            b"synthetic duplicate bytes"
        )
        addendum = source / "Addendum-01"
        addendum.mkdir()
        (addendum / "Addendum-01.pdf").write_bytes(b"%PDF synthetic addendum")
        before = {
            item: hashlib.sha256(item.read_bytes()).hexdigest()
            for item in sorted(source.rglob("*"))
            if item.is_file()
        }
        os.environ["CONTRACTIQ_DB_PATH"] = str(root / "app.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(root / "documents")
        os.environ["CONTRACTIQ_PROPOSAL_ARTIFACT_ROOT"] = str(root / "proposal")
        os.environ["CONTRACTIQ_INTAKE_STORAGE_ROOT"] = str(root / "intake")
        os.environ.pop("CONTRACTIQ_INTAKE_SOURCE_ROOTS", None)
        os.environ["CONTRACTIQ_INTAKE_ROOTS"] = str(source)
        previous = sys.modules.pop("app", None)
        original_connection = socket.create_connection
        socket.create_connection = lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("OPS-11BY attempted a network connection")
        )
        try:
            import app

            _seed(app)
            path = f"/bids/{BID_ID}/package-intake-addenda"
            audit_before = len(app.bid_repository.list_audit(BID_ID))

            status, _, selector = await request(app.app, "GET", f"{path}/import?mode=initial")
            measurements["user_actions"] += 1
            measurements["page_departures"] += 1
            assert status == 200
            assert source.as_posix().encode() not in selector, "an intake path was rendered"
            measurements["get_mutations"] += len(app.bid_repository.list_audit(BID_ID)) - (
                audit_before
            )

            status, _, form = await request(
                app.app,
                "GET",
                f"{path}/import?mode=initial&source_root_alias=inbox_1"
                "&source_folder=Initial-Package",
            )
            measurements["user_actions"] += 1
            measurements["page_departures"] += 1
            assert status == 200
            # The server records receipt time; the form must not ask for it.
            assert b'name="received_at"' not in form
            assert b"ContractIQ records the date and time of receipt itself" in form
            measurements["typed_fields"] += len(
                re.findall(
                    rb'<(?:input|select|textarea)[^>]*name="(?!source_|intake_|release_type)', form
                )
            )

            release_path = await _ingest(
                app, path, "Initial-Package", "INITIAL_PACKAGE", "RFP-BY-1"
            )
            measurements["user_actions"] += 2
            measurements["page_departures"] += 2
            release_id = release_path.rsplit("/", 1)[-1]
            registered = app.bid_package_repository.summary(BID_ID).releases[0]
            assert str(registered["received_at"]).startswith("20"), "receipt time was not recorded"

            status, _, detail = await request(app.app, "GET", release_path)
            assert status == 200
            # Reader-facing text only: identifiers in href/action URLs address, not inform.
            visible = re.sub(rb"<[^>]+>", b" ", detail.split(b"Advanced:")[0])
            # Nothing technical is put in front of the reader.
            assert b"Confidence" not in detail, "confidence asked for with no automated proposal"
            assert b"What does this addendum change?" not in detail, (
                "directives on an initial package"
            )
            assert b"Record acknowledgement event" not in detail, "acknowledgement in routine flow"
            assert b'type="file"' not in detail, "a second upload was demanded"
            for identifier in re.findall(rb"RF-[0-9a-f]{8}-", visible):
                measurements["record_identifiers_shown"] += 1
                raise AssertionError(f"a record identifier reached the reader: {identifier!r}")
            assert b"Not yet decided" in detail
            assert b"Review selected files" in detail
            assert b"Select all files in this folder" in detail
            assert b"received file(s) have not been reviewed yet" in detail

            files = app.bid_package_repository.release_detail(BID_ID, release_id)["files"]
            # The duplicate control appears only for the files that really are duplicates.
            duplicates = [
                item for item in files if b"Schedule" in str(item["original_filename"]).encode()
            ]
            assert len(duplicates) == 2
            unique = next(item for item in files if "Invitation" in str(item["original_filename"]))
            unique_block = detail.split(str(unique["file_id"]).encode())[1][:4000]
            assert b"This file is the same bytes as" not in unique_block, (
                "a duplicate selector was offered for a file with no duplicate"
            )
            assert b"This file is the same bytes as" in detail, (
                "the duplicate selector was hidden for a real duplicate"
            )

            # Exclusion reason ships hidden and is revealed by the browser only when Excluded.
            assert b"<label data-exclusion hidden>" in detail

            # One action puts a received file under document control, using the held original.
            documents_before = len(app.document_service.list_register_entries(bid_id=BID_ID))
            status, _, _ = await request(
                app.app,
                "POST",
                f"{path}/files/{unique['file_id']}/controlled-document",
                {
                    "release_id": release_id,
                    "operation_id": CONTROL_OPERATION,
                    "title": "Synthetic invitation letter",
                    "category": "SOLICITATION",
                    "version_label": "Original",
                },
            )
            assert status == 303
            measurements["user_actions"] += 1
            measurements["typed_fields"] += 2
            entries = app.document_service.list_register_entries(bid_id=BID_ID)
            assert len(entries) == documents_before + 1
            created = next(
                entry for entry in entries if entry.document.title == "Synthetic invitation letter"
            )
            # The controlled bytes are provably the received bytes.
            assert created.current_version.sha256_digest == unique["sha256"]
            linked = next(
                item
                for item in app.bid_package_repository.release_detail(BID_ID, release_id)["files"]
                if item["file_id"] == unique["file_id"]
            )
            assert linked["document_version_id"] == created.current_version.document_version_id

            # An addendum, and only an addendum, offers directives in the customer's language.
            addendum_path = await _ingest(app, path, "Addendum-01", "ADDENDUM", "Addendum 01")
            measurements["user_actions"] += 3
            measurements["page_departures"] += 3
            status, _, addendum_page = await request(app.app, "GET", addendum_path)
            assert status == 200
            assert b"What does this addendum change?" in addendum_page
            for wording in (
                b"New document",
                b"Replaces earlier document",
                b"Changes part of earlier document",
                b"Withdraws earlier document",
                b"No Bid impact",
                b"Needs review",
            ):
                assert wording in addendum_page, wording
            assert b'placeholder="CHANGE, ADD, WITHDRAW, CLARIFY"' not in addendum_page

            # The Bid basis speaks plainly and says what it is.
            status, _, intake = await request(app.app, "GET", path)
            assert status == 200
            assert b"Confirm current Bid basis" in intake
            assert b"Publish or update Bid Basis" not in intake
            assert b"the exact set of customer releases and documents" in intake

            after = {
                item: hashlib.sha256(item.read_bytes()).hexdigest()
                for item in sorted(source.rglob("*"))
                if item.is_file()
            }
            measurements["source_mutations"] += sum(
                1 for item, digest in before.items() if after.get(item) != digest
            )
        finally:
            socket.create_connection = original_connection
            sys.modules.pop("app", None)
            if previous is not None:
                sys.modules["app"] = previous
    assert measurements["get_mutations"] == 0
    assert measurements["source_mutations"] == 0
    assert measurements["re_uploads_of_held_files"] == 0
    assert measurements["receipt_times_typed"] == 0
    assert measurements["record_identifiers_shown"] == 0
    return measurements


if __name__ == "__main__":
    results = asyncio.run(main())
    for key, value in results.items():
        print(f"{key}: {value}")
    print("OPS-11BY intake simplification acceptance: PASS")
