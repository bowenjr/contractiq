"""Socketless rendered-control acceptance for OPS-09."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.asgi_acceptance_ops08 import request  # noqa: E402
from tests.unit.test_ops09 import BID_ID, generation_manifest, seed_ready_bid  # noqa: E402


def multipart(
    fields: dict[str, str], files: dict[str, tuple[str, str, bytes]]
) -> tuple[bytes, str]:
    """Create the small deterministic multipart bodies used by this acceptance."""
    boundary = "contractiq-ops09-acceptance"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            ).encode()
        )
    for name, (filename, media_type, file_content) in files.items():
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\nContent-Type: {media_type}\r\n\r\n'
            ).encode()
        )
        chunks.extend((file_content, b"\r\n"))
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


async def main() -> dict[str, int]:
    """Exercise actual browser controls and return observed workflow measurements."""
    measurements = {
        "user_actions": 0,
        "departures_from_bid_context": 0,
        "context_losses": 0,
        "duplicate_data_entry": 0,
        "prerequisite_surprises": 0,
        "dead_ends": 0,
        "raw_json_or_manual_url_requirements": 0,
    }
    with tempfile.TemporaryDirectory(prefix="contractiq-ops09-asgi-") as directory:
        root = Path(directory)
        os.environ["CONTRACTIQ_DB_PATH"] = str(root / "app.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(root / "documents")
        os.environ["CONTRACTIQ_PROPOSAL_ARTIFACT_ROOT"] = str(root / "artifacts")
        original_connection = socket.create_connection
        prior_app = sys.modules.pop("app", None)
        socket.create_connection = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("OPS-09 attempted a network connection")
        )
        try:
            import app

            seed_ready_bid(app.db, app.bid_repository)
            proposal_path = f"/bids/{BID_ID}/proposal-negotiation"
            control_path = f"/bids/{BID_ID}/proposal-issue-control"
            status, _, proposal_page = await request(app.app, "GET", proposal_path)
            assert status == 200 and control_path.encode() in proposal_page
            measurements["departures_from_bid_context"] += int(
                not proposal_path.startswith(f"/bids/{BID_ID}")
            )
            status, _, control_page = await request(app.app, "GET", control_path)
            assert status == 200 and BID_ID.encode() in control_page
            measurements["context_losses"] += int(BID_ID.encode() not in control_page)
            measurements["prerequisite_surprises"] += int(b"Prerequisite" not in control_page)

            audit_before_get = len(app.bid_repository.list_audit(BID_ID))
            status, export_headers, _ = await request(
                app.app, "POST", f"/bids/{BID_ID}/proposal-exports"
            )
            measurements["user_actions"] += 1
            assert status == 303 and export_headers["location"].endswith("/download")
            status, _, package_bytes = await request(app.app, "GET", export_headers["location"])
            assert status == 200
            package = json.loads(package_bytes)
            assert package["bid"]["bid_id"] == BID_ID
            audit_after_export = len(app.bid_repository.list_audit(BID_ID))
            assert audit_after_export == audit_before_get + 1

            export = app.proposal_exchange_service.history(BID_ID).exports[0]
            invalid_body, invalid_type = multipart(
                {"export_id": export.export_id},
                {
                    "manifest": (
                        "manifest.json",
                        "application/json",
                        b'{"duplicate":1,"duplicate":2}',
                    )
                },
            )
            status, _, retained_error = await request(
                app.app,
                "POST",
                f"/bids/{BID_ID}/proposal-manifests",
                body=invalid_body,
                content_type=invalid_type,
            )
            measurements["user_actions"] += 1
            assert status == 422
            assert (
                b"Duplicate JSON object member" in retained_error
                and BID_ID.encode() in retained_error
            )
            assert len(app.bid_repository.list_audit(BID_ID)) == audit_after_export

            docx = b"OPS-09 controlled DOCX bytes"
            pdf = b"%PDF OPS-09 controlled PDF bytes"
            manifest = generation_manifest(
                export.package_id,
                export.canonical_sha256,
                docx,
                pdf,
            )
            valid_body, valid_type = multipart(
                {"export_id": export.export_id},
                {
                    "manifest": (
                        "proposal-generation-manifest-v1.json",
                        "application/json",
                        json.dumps(manifest).encode(),
                    ),
                    "docx": (
                        "proposal-A.docx",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        docx,
                    ),
                    "pdf": ("proposal-A.pdf", "application/pdf", pdf),
                },
            )
            status, import_headers, _ = await request(
                app.app,
                "POST",
                f"/bids/{BID_ID}/proposal-manifests",
                body=valid_body,
                content_type=valid_type,
            )
            measurements["user_actions"] += 1
            assert status == 303 and import_headers["location"].endswith("#approve")
            candidate = app.proposal_exchange_service.history(BID_ID).candidates[0]

            status, approve_headers, _ = await request(
                app.app,
                "POST",
                f"/bids/{BID_ID}/proposal-candidates/{candidate.candidate_id}/approve",
                {"expected_version": str(candidate.version)},
            )
            measurements["user_actions"] += 1
            assert status == 303 and approve_headers["location"].endswith("#record-issue")
            approved = app.proposal_exchange_service.history(BID_ID).candidates[0]
            issue_form = {
                "expected_version": str(approved.version),
                "issue_revision": "A",
                "issued_at": "2026-09-10T09:00",
                "issue_method": "Customer portal",
                "destination_reference": "Portal receipt OPS09-001",
                "offer_valid_until": "2026-10-10",
                "note": "Manual customer upload recorded.",
            }
            status, issue_headers, _ = await request(
                app.app,
                "POST",
                f"/bids/{BID_ID}/proposal-candidates/{candidate.candidate_id}/issue",
                issue_form,
            )
            measurements["user_actions"] += 1
            assert status == 303 and issue_headers["location"].endswith("#history")

            status, _, issued_page = await request(app.app, "GET", control_path)
            assert (
                status == 200
                and b"Issued" in issued_page
                and b"Portal receipt OPS09-001" in issued_page
            )
            status, _, handover = await request(app.app, "GET", f"/bids/{BID_ID}/handover")
            assert status == 200 and b"Revision A" in handover
            assert export.canonical_sha256.encode() in handover
            measurements["context_losses"] += int(BID_ID.encode() not in handover)
            measurements["duplicate_data_entry"] += int(
                b'name="customer"' in issued_page or b'name="project_name"' in issued_page
            )
            measurements["dead_ends"] += int(
                f"/bids/{BID_ID}/proposal-negotiation".encode() not in issued_page
            )
            measurements["raw_json_or_manual_url_requirements"] += int(
                b'name="destination_reference" type="url"' in issued_page
            )
            with app.db._conn() as conn:
                assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert (
                    conn.execute("SELECT count(*) FROM issued_offer_baselines").fetchone()[0] == 1
                )
            assert hashlib.sha256(docx).hexdigest().encode() in issued_page
        finally:
            socket.create_connection = original_connection
            sys.modules.pop("app", None)
            if prior_app is not None:
                sys.modules["app"] = prior_app
    for label, value in measurements.items():
        print(f"{label.replace('_', ' ')}: {value}")
    return measurements


if __name__ == "__main__":
    asyncio.run(main())
    print("OPS-09 ASGI acceptance: PASS")
