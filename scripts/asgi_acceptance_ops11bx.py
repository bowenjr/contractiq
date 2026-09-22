"""Socketless rendered walkthrough for the OPS-11BX Bid workflow spine.

This drives the real ASGI application through one whole Bid, from creation to
handover, using only synthetic bytes. Every count it reports is measured while
the controls execute; nothing is asserted from a hard-coded number.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.asgi_acceptance_ops08 import multipart, request  # noqa: E402

PRIMARY = re.compile(rb'<a class="button-lg" href="([^"]+)">([^<]+)</a>')
STAGE_ROW = re.compile(
    rb'<span class="stage-no">(\d+)</span>\s*'
    rb'<a class="stage-label" href="[^"]+">([^<]+)</a>\s*'
    rb'<span><span class="s-badge [^"]*">([^<]+)</span>',
    re.S,
)
STAGE_LABELS = (
    "Bid setup",
    "Package intake and addenda",
    "Controlled customer documents",
    "Requirements and responses",
    "Scope and interfaces",
    "Manufacturers and supplier coverage",
    "Commercial, contract risk and approvals",
    "Proposal and negotiation",
    "Bid Basis and handover",
)


class Walk:
    """Records every measured control, departure and mutation as it happens."""

    def __init__(self, application: Any, audit: Any) -> None:
        self.app = application
        self._audit = audit
        self.actions = 0
        self.departures = 0
        self.get_mutations = 0
        self.context_losses = 0
        self.raw_json_or_manual_url = 0
        self._path = ""

    async def get(self, path: str, *, action: bool = True) -> bytes:
        before = len(self._audit())
        status, headers, body = await request(self.app, "GET", path)
        assert status == 200, (path, status)
        assert headers["content-type"].startswith("text/html"), path
        self.get_mutations += len(self._audit()) - before
        if action:
            self.actions += 1
        self._departed(path)
        if b'{"detail"' in body:
            self.raw_json_or_manual_url += 1
        return body

    async def post(self, path: str, form: dict[str, str], *, expect: int = 303) -> str:
        status, headers, body = await request(self.app, "POST", path, form)
        assert status == expect, (path, status, body[:400])
        assert b'{"detail"' not in body, path
        self.actions += 1
        return headers.get("location", "")

    async def post_multipart(
        self, path: str, fields: dict[str, str], name: str, data: bytes
    ) -> str:
        body, content_type = multipart(fields, name, data)
        status, headers, content = await request(
            self.app, "POST", path, body=body, content_type=content_type
        )
        assert status == 303, (path, status, content[:400])
        assert b'{"detail"' not in content, path
        self.actions += 1
        return headers.get("location", "")

    def _departed(self, path: str) -> None:
        page = path.split("?", 1)[0].split("#", 1)[0]
        if self._path and page != self._path:
            self.departures += 1
        self._path = page


def primary_action(page: bytes) -> tuple[str, str]:
    """Return the one primary next action, asserting that no other competes."""
    found = PRIMARY.findall(page)
    assert len(found) == 1, f"expected exactly one primary action, found {len(found)}"
    href, label = found[0]
    return href.decode(), label.decode()


def stages(page: bytes) -> dict[str, str]:
    rows = STAGE_ROW.findall(page)
    assert len(rows) == 9, f"expected the nine-stage spine, found {len(rows)}"
    assert [row[1].decode() for row in rows] == list(STAGE_LABELS)
    return {row[1].decode(): row[2].decode() for row in rows}


def _synthetic_inbox(root: Path) -> Path:
    inbox = root / "intake-inbox"
    initial = inbox / "Initial-Package"
    (initial / "Commercial").mkdir(parents=True)
    (initial / "Technical").mkdir(parents=True)
    (initial / "Commercial" / "Invitation-to-Bid.docx").write_bytes(b"synthetic DOCX invitation")
    (initial / "Commercial" / "Duplicate-Package.zip").write_bytes(b"PK\x03\x04synthetic inert")
    (initial / "Technical" / "Technical-Document-Register.xlsx").write_bytes(b"synthetic XLSX")
    addendum = inbox / "Addendum-01"
    addendum.mkdir()
    (addendum / "Addendum-01.docx").write_bytes(b"synthetic addendum bytes")
    return inbox


async def main() -> dict[str, int]:  # noqa: C901 - one linear rendered walkthrough
    with tempfile.TemporaryDirectory(prefix="contractiq-ops11bx-") as directory:
        root = Path(directory)
        inbox = _synthetic_inbox(root)
        os.environ["CONTRACTIQ_DB_PATH"] = str(root / "app.db")
        os.environ["CONTRACTIQ_DOCUMENT_ROOT"] = str(root / "documents")
        os.environ["CONTRACTIQ_PROPOSAL_ARTIFACT_ROOT"] = str(root / "proposal")
        os.environ["CONTRACTIQ_INTAKE_STORAGE_ROOT"] = str(root / "intake")
        os.environ.pop("CONTRACTIQ_INTAKE_SOURCE_ROOTS", None)
        os.environ["CONTRACTIQ_INTAKE_ROOTS"] = str(inbox)
        sys.modules.pop("app", None)
        original_connection = socket.create_connection
        socket.create_connection = lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("OPS-11BX attempted a network connection")
        )
        try:
            import app

            walk = Walk(app.app, lambda: app.bid_repository.list_audit())

            # 1. Create a new Bid through the rendered form.
            location = await walk.post(
                "/bids",
                {
                    "project_name": "OPS-11BX synthetic workflow",
                    "customer": "Synthetic Mining Co",
                    "customer_type": "end_user",
                    "sales_owner": "Sam Sales",
                    "bc_owner": "Jason",
                    "release_date": "2026-09-01",
                    "customer_due_date": "2026-11-01",
                    "internal_due_date": "2026-10-20",
                    "estimated_value": "250000",
                    "currency": "CAD",
                },
            )
            bid = location.rsplit("/", 1)[-1]
            workspace = f"/bids/{bid}"
            intake_path = f"{workspace}/package-intake-addenda"
            actions_after_creation = walk.actions

            # 2. The most prominent action is package ingestion.
            page = await walk.get(workspace, action=False)
            href, label = primary_action(page)
            assert label == "Import customer bid package", label
            assert href == f"{intake_path}/import?mode=initial", href
            assert stages(page)["Package intake and addenda"] == "Not started"
            assert b"Register customer source" not in page
            assert b"Complete Bid intake" not in page
            assert b"Bid/no-bid approval has been obtained." not in page
            assert b"Bid/no-bid approval missing" in page

            # 3. Import the nested synthetic package.
            selector = await walk.get(href)
            actions_to_selector = walk.actions - actions_after_creation
            assert b"Initial-Package" in selector and b"Addendum-01" in selector
            assert str(inbox).encode() not in selector
            await walk.get(
                f"{intake_path}/import?mode=initial"
                "&source_root_alias=inbox_1&source_folder=Initial-Package"
            )
            preview_body = await _preview(walk, intake_path, "Initial-Package", "INITIAL_PACKAGE")
            assert b"Commercial/Invitation-to-Bid.docx" in preview_body
            assert b"Technical/Technical-Document-Register.xlsx" in preview_body
            assert b"Commercial/Duplicate-Package.zip" in preview_body
            token = re.search(rb'name="confirmation_token" value="([^"]+)"', preview_body)
            assert token is not None
            release_location = await walk.post(
                f"{intake_path}/register", {"confirmation_token": token.group(1).decode()}
            )

            # 4. The import returns to the same Bid.
            assert release_location.startswith(f"{intake_path}/releases/"), release_location
            release_id = release_location.rsplit("/", 1)[-1]
            page = await walk.get(release_location, action=False)
            assert bid.encode() in page

            # 5. Package intake advanced from Not started.
            state = stages(page)
            assert state["Package intake and addenda"] == "In progress", state

            # 6. Review and control of the imported documents is the next action.
            href, label = primary_action(page)
            assert label == "Review received package files", label
            assert release_id in href and href.endswith("#files"), href

            detail = app.bid_package_repository.release_detail(bid, release_id)
            files = list(detail["files"])
            for row in files:
                await walk.post(
                    f"{intake_path}/files/{row['file_id']}/dispositions",
                    {
                        "release_id": release_id,
                        "supersedes_event_id": str(row["disposition_event_id"]),
                        "operation_id": f"OP-REVIEW-{row['file_id']}",
                        "content_form": "TEXTUAL",
                        "analysis_eligibility": "ELIGIBLE",
                        "confidence": "1",
                    },
                )
            page = await walk.get(release_location, action=False)
            assert stages(page)["Package intake and addenda"] == "Complete"
            href, label = primary_action(page)
            assert label == "Register a controlled customer document", label

            # 7. Create a controlled source and link it to a received file.
            requirements_page = await walk.get(f"/requirements?bid_id={bid}")
            assert f"Back to Bid {bid}".encode() in requirements_page
            operation = re.search(
                rb'name="source_operation_token" value="([^"]+)"', requirements_page
            )
            assert operation is not None

            # 8. Add a requirement together with its proposed response.
            fields = {
                "bid_id": bid,
                "source_operation_token": operation.group(1).decode(),
                "origin": "EXPLICIT",
                "category": "TECHNICAL",
                "significance": "MANDATORY",
                "lifecycle_stage": "BID",
                "title": "Pump seal compliance",
                "statement": "Vendor shall confirm the pump seal plan meets the specification.",
                "response_text": "Compliant subject to written manufacturer confirmation.",
                "disposition": "CLARIFY",
                "work_state": "OPEN",
                "owner": "Jason",
                "contributor": "Engineering",
                "reviewer": "Commercial / Contracts",
                "source_clause": "Section 4.2",
                "new_source_title": "Customer Mechanical Specification",
                "new_source_version_label": "Revision 0",
            }
            staged = await walk.post_multipart(
                "/requirements/with-source", fields, "customer-spec.pdf", b"%PDF synthetic"
            )
            retained = await walk.get(staged)
            source = re.search(
                rb'<option value="([^"]+)" selected>Customer Mechanical Specification', retained
            )
            form_state = re.search(rb'name="form_state" value="([^"]+)"', retained)
            assert source is not None and form_state is not None
            final = dict(fields)
            final["source_document_version_id"] = source.group(1).decode()
            final["form_state"] = form_state.group(1).decode()
            requirement_location = await walk.post_multipart("/requirements", final, "", b"")
            requirement_id = requirement_location.rsplit("/", 1)[-1]

            version_id = source.group(1).decode()
            await walk.post(
                f"{intake_path}/files/{files[0]['file_id']}/document-links",
                {
                    "release_id": release_id,
                    "supersedes_link_id": "",
                    "operation_id": "OP-LINK-1",
                    "document_version_id": version_id,
                    "relationship": "SUPPORTING_EVIDENCE",
                },
            )
            page = await walk.get(workspace, action=False)
            state = stages(page)
            assert state["Controlled customer documents"] == "Complete", state
            assert state["Requirements and responses"] == "Complete", state

            # 9. Add scope and interface information.
            await walk.post(
                "/scope-items",
                {
                    "bid_id": bid,
                    "title": "Pump package supply",
                    "description": "Supply and technical compliance",
                    "scope_area": "CORE_PRODUCTS",
                    "origin": "REQUIREMENT_DERIVED",
                    "customer_need": "REQUIRED",
                    "offer_position": "INCLUDED",
                    "pricing_state": "UNCONFIRMED",
                    "materiality": "MATERIAL",
                    "owner": "Jason",
                    "requirement_id": requirement_id,
                },
            )
            scope = app.scope_repository.list_scope_items(bid)[0]
            await walk.post(
                "/interfaces",
                {
                    "bid_id": bid,
                    "title": "Customer foundation data",
                    "boundary_description": "Mechanical mounting boundary",
                    "upstream_party": "Customer",
                    "downstream_party": "Engineering",
                    "dependency_description": "Final foundation loads",
                    "materiality": "MATERIAL",
                    "dependency_state": "OPEN",
                    "owner": "Jason",
                    "scope_item_id": scope.scope_item_id,
                },
            )

            # 10. Add manufacturer and supplier coverage.
            await walk.post(
                f"/requirements/{requirement_id}/manufacturer-packages",
                {
                    "package_code": "PMP-01",
                    "package_name": "Pump package — Synthetic Manufacturer",
                    "proposed_manufacturer": "Synthetic Manufacturer",
                    "internal_owner": "Jason",
                },
            )
            package = app.vendor_document_repository.list_packages(bid)[0]
            await walk.post(
                f"/vendor-documents/packages/{package.package_id}/requirements",
                {
                    "customer_requirement_code": "SEAL-01",
                    "deliverable_title": "Manufacturer compliance letter",
                },
            )
            verification = app.vendor_document_repository.list_requirements(package.package_id)[0]
            await walk.post(
                f"/requirements/{requirement_id}/verification-links",
                {"verification_row_id": verification.requirement_id},
            )
            await walk.post(
                f"/vendor-documents/requirements/{verification.requirement_id}",
                {
                    "expected_version": "1",
                    "verification_status": "CONFIRMED_COMPLIANT",
                    "proposed_manufacturer": "Synthetic Manufacturer",
                    "response_source": "Manufacturer compliance letter",
                    "response_received_date": "2026-09-01",
                    "evidence_reference": "Written manufacturer confirmation",
                    "internal_owner": "Jason",
                    "commercial_impact": "NONE",
                    "bid_disposition": "NONE",
                },
            )

            # 11. Add commercial, contract risk and approval information.
            for path, form in (
                (
                    "/commercial",
                    {"bid_id": bid, "operation": "initialize", "default_owner": "Jason"},
                ),
                (
                    "/contract-risks",
                    {
                        "bid_id": bid,
                        "issue_code": "LD-01",
                        "title": "Liquidated damages",
                        "summary": "Customer LD position requires review",
                        "owner": "Commercial / Contracts",
                    },
                ),
                (
                    "/decisions/gate-approvals",
                    {
                        "bid_id": bid,
                        "approval_type": "bid_no_bid",
                        "obtained": "on",
                        "authority": "Director",
                        "evidence_ref": "Approval minute",
                        "decision": "Proceed",
                    },
                ),
                (
                    "/decisions/gate-approvals",
                    {
                        "bid_id": bid,
                        "approval_type": "margin",
                        "obtained": "on",
                        "authority": "Finance",
                        "evidence_ref": "Margin approval minute",
                        "decision": "Approved",
                    },
                ),
                (
                    "/decisions/cases",
                    {
                        "bid_id": bid,
                        "case_code": "CASE-01",
                        "decision_type": "CONTRACT_POSITION",
                        "title": "Contract risk position",
                        "owner": "Commercial / Contracts",
                    },
                ),
            ):
                await walk.post(path, form)

            # 12. Exercise proposal readiness.
            for path, form in (
                (
                    "/proposals/families",
                    {
                        "bid_id": bid,
                        "code": "PROP-01",
                        "title": "Technical and commercial proposal",
                        "applicability": "PROPOSAL_REQUIRED",
                        "owner": "Jason",
                    },
                ),
                (
                    "/deliverables",
                    {
                        "bid_id": bid,
                        "title": "Manufacturer compliance letter",
                        "description": "Pre-award compliance evidence",
                        "category": "Technical",
                        "criticality": "MANDATORY",
                        "direction": "COMPANY_TO_CUSTOMER",
                        "owner": "Jason",
                    },
                ),
            ):
                await walk.post(path, form)
            proposal_page = await walk.get(f"{workspace}/proposal-negotiation")
            assert b"Proposal readiness" in proposal_page

            # 13. Bid Basis and handover remains the final stage.
            page = await walk.get(workspace, action=False)
            state = stages(page)
            assert list(state)[-1] == "Bid Basis and handover"
            assert state["Bid Basis and handover"] in {
                "Blocked",
                "In progress",
                "Ready for review",
            }, state

            # 14 and 15. Every Bid workspace section keeps Bid context, one spine,
            # one primary action and no raw JSON or manual URL.
            sections = (
                workspace,
                intake_path,
                f"{workspace}/requirements-scope",
                f"{workspace}/manufacturers-coverage",
                f"{workspace}/commercial-contract",
                f"{workspace}/proposal-negotiation",
                f"{workspace}/award-handover",
            )
            navigators: set[bytes] = set()
            for path in sections:
                body = await walk.get(path)
                if bid.encode() not in body:
                    walk.context_losses += 1
                stages(body)
                primary_action(body)
                navigator = re.search(rb'aria-label="Bid workflow stages".*?</nav>', body, re.S)
                assert navigator is not None, path
                navigators.add(
                    navigator.group(0)
                    .replace(b" viewing", b"")
                    .replace(b' aria-current="true"', b"")
                )
                # Two spine stages can share one workspace section, but exactly one
                # row may be marked as the position being viewed.
                assert navigator.group(0).count(b'aria-current="true"') == 1, path
            # 16. One identical spine everywhere, with one primary action each.
            assert len(navigators) == 1, "the workflow navigator differs between sections"

            # Registers reached from the Bid return to the same Bid.
            for path in (
                f"/requirements?bid_id={bid}",
                f"/scope-interfaces?bid_id={bid}",
                f"/documents?bid_id={bid}",
                f"/commercial?bid_id={bid}",
                f"/contract-risks?bid_id={bid}",
                f"/decisions?bid_id={bid}",
                f"/proposals?bid_id={bid}",
                f"/deliverables?bid_id={bid}",
                f"/vendor-documents?bid_id={bid}",
            ):
                body = await walk.get(path)
                if f"Back to Bid {bid}".encode() not in body:
                    walk.context_losses += 1

            # Every stage action the spine advertises resolves to a real anchor, so
            # no primary action drops the user at the top of an unrelated register.
            for target, anchor in _stage_action_anchors(await walk.get(workspace, action=False)):
                body = await walk.get(target, action=False)
                if anchor:
                    assert f'id="{anchor}"'.encode() in body, (target, anchor)

            # A blocker is stated as what is missing, never as the satisfied
            # condition, on every surface that lists one.
            for path in (
                workspace,
                f"{workspace}/proposal-issue-control",
                f"/bids/{bid}/handover",
                "/my-day",
            ):
                body = await walk.get(path, action=False)
                assert b"Bid/no-bid approval has been obtained." not in body, path

            # 17. GET navigation created no mutation and no audit record.
            assert walk.get_mutations == 0, walk.get_mutations
            assert walk.context_losses == 0, walk.context_losses
            assert walk.raw_json_or_manual_url == 0, walk.raw_json_or_manual_url

            # The synthetic source folder was never mutated.
            assert sorted(p.name for p in inbox.iterdir()) == ["Addendum-01", "Initial-Package"]

            return {
                "measured_user_actions": walk.actions,
                "measured_page_departures": walk.departures,
                "get_mutations": walk.get_mutations,
                "context_losses": walk.context_losses,
                "raw_json_or_manual_url_requirements": walk.raw_json_or_manual_url,
                "actions_from_creation_to_package_selector": actions_to_selector,
            }
        finally:
            socket.create_connection = original_connection
            sys.modules.pop("app", None)


def _stage_action_anchors(page: bytes) -> list[tuple[str, str]]:
    """Return every stage action link on the spine with the anchor it targets."""
    found: list[tuple[str, str]] = []
    for match in re.finditer(rb'<a class="stage-action" href="([^"]+)"', page):
        href = match.group(1).decode().replace("&amp;", "&")
        path, _, anchor = href.partition("#")
        found.append((path, anchor))
    return found


async def _preview(walk: Walk, intake_path: str, folder: str, release_type: str) -> bytes:
    status, _, body = await request(
        walk.app,
        "POST",
        f"{intake_path}/preview",
        {
            "release_type": release_type,
            "source_root_alias": "inbox_1",
            "source_folder": folder,
            "intake_mode": "initial" if release_type == "INITIAL_PACKAGE" else "addendum",
            "exact_customer_reference": f"SYN-{folder}",
            "customer_issue_date": "2026-09-10",
            "received_at": "2026-09-11T09:00",
            "received_channel": "EMAIL",
        },
    )
    assert status == 200 and f"Preview: {folder}".encode() in body
    walk.actions += 1
    return body


if __name__ == "__main__":
    measurements = asyncio.run(main())
    for key, value in measurements.items():
        print(f"{key}: {value}")
    print("OPS-11BX rendered workflow acceptance: PASS")
