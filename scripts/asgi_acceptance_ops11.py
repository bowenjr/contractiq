"""Dependency-free socketless browser acceptance for OPS-11 Bid package intake."""

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
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.enums import BidLevel, BidStatus, CustomerType, Gate  # noqa: E402
from core.schemas import Bid, Provenance  # noqa: E402
from scripts.asgi_acceptance_ops08 import request  # noqa: E402

BID_ID = "B-2026-1111"


def _seed(app_module: object, content: bytes) -> str:
    now = datetime(2026, 9, 11, 12, tzinfo=UTC)
    app_module.bid_repository.create_bid(  # type: ignore[attr-defined]
        Bid(
            bid_id=BID_ID,
            customer="Synthetic EPC",
            customer_type=CustomerType.EPC,
            project_name="OPS-11 synthetic acceptance",
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
    document_id = "DOC-00000000-0000-0000-0000-000000001111"
    version_id = "DV-00000000-0000-0000-0000-000000001111"
    provenance = Provenance.from_human("Jason").model_dump_json()
    with app_module.db._conn() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """INSERT INTO documents(id,filename,bid_id,control_managed,control_title,
            document_number,document_category,control_lifecycle,current_version_id,
            control_created_at,control_updated_at,control_version,control_provenance_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                document_id,
                "Invitation-to-Bid.docx",
                BID_ID,
                1,
                "Synthetic controlled RFP",
                "SYN-RFP-1",
                "SOLICITATION",
                "ACTIVE",
                version_id,
                now.isoformat(),
                now.isoformat(),
                1,
                provenance,
            ),
        )
        conn.execute(
            """INSERT INTO document_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                version_id,
                document_id,
                "A",
                "2026-09-10",
                now.isoformat(),
                "synthetic.pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                len(content),
                hashlib.sha256(content).hexdigest(),
                "versions/11/synthetic.bin",
                None,
                "CURRENT",
                now.isoformat(),
                provenance,
            ),
        )
    return version_id


async def main() -> dict[str, int]:
    measurements = {
        "user_actions": 0,
        "context_losses": 0,
        "raw_json_or_manual_url_requirements": 0,
        "get_mutations": 0,
        "source_mutations": 0,
    }
    with tempfile.TemporaryDirectory(prefix="contractiq-ops11-asgi-") as directory:
        root = Path(directory)
        source = root / "intake-inbox"
        source.mkdir()
        content = b"synthetic DOCX acceptance evidence"
        source_file = source / "Initial-Package" / "Commercial" / "Invitation-to-Bid.docx"
        source_file.parent.mkdir(parents=True)
        source_file.write_bytes(content)
        (source / "Initial-Package" / "Commercial" / "Duplicate-Package.zip").write_bytes(
            b"PK\x03\x04synthetic inert archive"
        )
        technical_file = (
            source / "Initial-Package" / "Technical" / "Technical-Document-Register.xlsx"
        )
        technical_file.parent.mkdir(parents=True)
        technical_file.write_bytes(b"synthetic XLSX acceptance evidence")
        addendum_file = source / "Addendum-01" / "Addendum-01.docx"
        addendum_file.parent.mkdir(parents=True)
        addendum_file.write_bytes(b"synthetic addendum evidence")
        source_before = hashlib.sha256(source_file.read_bytes()).hexdigest()
        os.environ["CONTRACTIQ_DB_PATH"] = str(root / "app.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(root / "documents")
        os.environ["CONTRACTIQ_PROPOSAL_ARTIFACT_ROOT"] = str(root / "proposal")
        os.environ["CONTRACTIQ_INTAKE_STORAGE_ROOT"] = str(root / "intake")
        os.environ.pop("CONTRACTIQ_INTAKE_SOURCE_ROOTS", None)
        os.environ["CONTRACTIQ_INTAKE_ROOTS"] = str(source)
        previous = sys.modules.pop("app", None)
        original_connection = socket.create_connection
        socket.create_connection = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("OPS-11 attempted a network connection")
        )
        try:
            import app

            version_id = _seed(app, content)
            path = f"/bids/{BID_ID}/package-intake-addenda"
            audit_before = len(app.bid_repository.list_audit(BID_ID))
            status, _, page = await request(app.app, "GET", path)
            assert status == 200 and b"Package intake and addenda" in page
            assert b"Import customer bid package" in page
            assert b"Record Portal / Email Check" not in page
            assert b"Prepare Work AI Package" not in page
            assert source.as_posix().encode() not in page
            measurements["get_mutations"] += (
                len(app.bid_repository.list_audit(BID_ID)) - audit_before
            )
            status, _, inbox = await request(app.app, "GET", f"{path}/import?mode=initial")
            measurements["user_actions"] += 1
            assert status == 200 and b"Initial-Package" in inbox and b"Addendum-01" in inbox
            assert b"Commercial/Invitation-to-Bid.docx" not in inbox
            assert source.as_posix().encode() not in inbox
            assert len(app.bid_repository.list_audit(BID_ID)) == audit_before
            status, _, selected = await request(
                app.app,
                "GET",
                f"{path}/import?mode=initial&source_root_alias=inbox_1&source_folder=Initial-Package",
            )
            measurements["user_actions"] += 1
            assert status == 200 and b"Selected folder: Initial-Package" in selected
            assert len(app.bid_repository.list_audit(BID_ID)) == audit_before
            status, _, preview = await request(
                app.app,
                "POST",
                f"{path}/preview",
                {
                    "release_type": "INITIAL_PACKAGE",
                    "source_root_alias": "inbox_1",
                    "source_folder": "Initial-Package",
                    "intake_mode": "initial",
                    "exact_customer_reference": "RFP-SYN-1",
                    "customer_issue_date": "2026-09-10",
                    "received_at": "2026-09-11T09:00",
                    "received_channel": "PORTAL",
                },
            )
            measurements["user_actions"] += 1
            assert status == 200 and b"Preview: Initial-Package" in preview
            assert b"Commercial/Invitation-to-Bid.docx" in preview
            assert b"Technical/Technical-Document-Register.xlsx" in preview
            assert b"Commercial/Duplicate-Package.zip" in preview
            assert len(app.bid_repository.list_audit(BID_ID)) == audit_before
            token = re.search(rb'name="confirmation_token" value="([^"]+)"', preview)
            assert token is not None
            status, headers, _ = await request(
                app.app,
                "POST",
                f"{path}/register",
                {"confirmation_token": token.group(1).decode()},
            )
            measurements["user_actions"] += 1
            assert status == 303 and headers["location"].startswith(path + "/releases/")
            release = app.bid_package_repository.summary(BID_ID).releases[0]
            release_id = str(release["release_id"])
            detail = app.bid_package_repository.release_detail(BID_ID, release_id)
            files = cast(list[dict[str, object]], detail["files"])
            file_row = next(
                row for row in files if row["original_filename"] == "Invitation-to-Bid.docx"
            )
            file_id = str(file_row["file_id"])
            status, _, _ = await request(
                app.app,
                "POST",
                f"{path}/files/{file_id}/dispositions",
                {
                    "release_id": release_id,
                    "content_form": "TEXTUAL",
                    "analysis_eligibility": "ELIGIBLE",
                    "confidence": "1",
                    "supersedes_event_id": str(file_row["disposition_event_id"]),
                    "operation_id": "accept-classify",
                },
            )
            measurements["user_actions"] += 1
            assert status == 303
            assert app.bid_package_repository.summary(BID_ID).notices == ()
            status, _, _ = await request(
                app.app,
                "POST",
                f"{path}/files/{file_id}/document-links",
                {
                    "release_id": release_id,
                    "document_version_id": version_id,
                    "relationship": "EXACT_BYTES",
                    "operation_id": "accept-link",
                },
            )
            measurements["user_actions"] += 1
            assert status == 303
            for extra_file in files:
                if extra_file["file_id"] == file_id:
                    continue
                status, _, _ = await request(
                    app.app,
                    "POST",
                    f"{path}/files/{extra_file['file_id']}/dispositions",
                    {
                        "release_id": release_id,
                        "content_form": "UNKNOWN",
                        "analysis_eligibility": "EXCLUDED",
                        "exclusion_reason": "Synthetic acceptance exclusion",
                        "confidence": "1",
                        "supersedes_event_id": str(extra_file["disposition_event_id"]),
                        "operation_id": f"accept-exclude-{extra_file['file_id']}",
                    },
                )
                measurements["user_actions"] += 1
                assert status == 303
            status, _, addendum_preview = await request(
                app.app,
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
            measurements["user_actions"] += 1
            assert status == 200 and b"Preview: Addendum-01" in addendum_preview
            addendum_token = re.search(
                rb'name="confirmation_token" value="([^"]+)"', addendum_preview
            )
            assert addendum_token is not None
            status, _, _ = await request(
                app.app,
                "POST",
                f"{path}/register",
                {"confirmation_token": addendum_token.group(1).decode()},
            )
            measurements["user_actions"] += 1
            assert status == 303
            assert len(app.bid_package_repository.summary(BID_ID).releases) == 2
            status, _, _ = await request(
                app.app,
                "POST",
                f"{path}/basis-snapshots",
                {
                    "release_ids": release_id,
                    "label": "Synthetic controlled Bid Basis",
                    "operation_id": "accept-basis",
                },
            )
            measurements["user_actions"] += 1
            assert status == 303
            status, _, register = await request(app.app, "GET", f"{path}/bid-basis-register")
            assert status == 200 and b"SYN-RFP-1" in register
            status, _, csv_bytes = await request(app.app, "GET", f"{path}/bid-basis-register.csv")
            assert status == 200 and b"RFP-SYN-1" in csv_bytes
            measurements["context_losses"] += int(BID_ID.encode() not in register)
            measurements["source_mutations"] += int(
                hashlib.sha256(source_file.read_bytes()).hexdigest() != source_before
            )
            with app.db._conn() as conn:
                assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert all(value == 0 for key, value in measurements.items() if key != "user_actions")
            assert measurements["user_actions"] == 11
            return measurements
        finally:
            socket.create_connection = original_connection
            sys.modules.pop("app", None)
            if previous is not None:
                sys.modules["app"] = previous


if __name__ == "__main__":
    print(asyncio.run(main()))
