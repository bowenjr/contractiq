"""Dependency-free ASGI route matrix for OPS-07 relationship mutations."""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlencode, urlsplit

import pytest

from core.schemas import Provenance
from core.scope_interfaces import Materiality, ScopeArea, ScopeItem, ScopeOrigin
from core.vendor_document_control import SupplierPackageCreate


async def _request(
    application: Any,
    method: str,
    path: str,
    form: dict[str, str] | None = None,
    *,
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    sent: list[dict[str, Any]] = []
    payload = body if body is not None else urlencode(form or {}).encode()
    incoming = [{"type": "http.request", "body": payload, "more_body": False}]

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
            "headers": [
                (
                    b"content-type",
                    (content_type or "application/x-www-form-urlencoded").encode(),
                )
            ]
            if method == "POST"
            else [],
        },
        receive,
        send,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    headers = {
        bytes(key).decode().lower(): bytes(value).decode() for key, value in start["headers"]
    }
    response = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return int(start["status"]), headers, response


def _multipart(fields: dict[str, str], filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = "ops07w-route-test"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            ).encode()
        )
    chunks.extend(
        [
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="new_source_file"; '
                f'filename="{filename}"\r\nContent-Type: application/pdf\r\n\r\n'
            ).encode(),
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


@pytest.fixture
def ops07_route_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "routes.db"))
    monkeypatch.setenv("CONTRACTIQ_DOCUMENT_ROOT", str(tmp_path / "documents"))
    sys.modules.pop("app", None)
    app_path = Path(__file__).parents[2] / "app.py"
    spec = importlib.util.spec_from_file_location("app", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    return module


def _requirement(module: ModuleType, bid_id: str, title: str):
    return module.requirement_service.create_requirement(
        {
            "bid_id": bid_id,
            "title": title,
            "statement": title,
            "origin": "INTERNAL",
            "category": "TECHNICAL",
            "significance": "MANDATORY",
        },
        "Jason",
    )


def _scope(module: ModuleType, bid_id: str, title: str) -> ScopeItem:
    now = datetime.now(UTC)
    item = ScopeItem(
        bid_id=bid_id,
        title=title,
        description=title,
        scope_area=ScopeArea.CORE_PRODUCTS,
        origin=ScopeOrigin.INTERNAL,
        materiality=Materiality.MATERIAL,
        created_at=now,
        updated_at=now,
        provenance=Provenance.from_human("Jason"),
        created_by="Jason",
    )
    return module.scope_service.create_scope_item(item, "Jason")


def _package(module: ModuleType, bid_id: str, code: str):
    return module.vendor_document_service.create_package(
        SupplierPackageCreate(
            bid_id=bid_id,
            package_name=code,
            package_code=code,
            proposed_manufacturer="Manufacturer",
            internal_owner="Jason",
        ),
        "Jason",
    )


def _audit_count(module: ModuleType) -> int:
    return len(module.bid_repository.list_audit())


def test_scope_and_manufacturer_route_error_matrix(ops07_route_app: ModuleType, valid_bid) -> None:
    module = ops07_route_app
    module.bid_repository.create_bid(valid_bid)
    other = valid_bid.model_copy(update={"bid_id": "B-2026-9999"})
    module.bid_repository.create_bid(other)
    requirement = _requirement(module, valid_bid.bid_id, "Requirement")
    scope = _scope(module, valid_bid.bid_id, "Scope")
    foreign_scope = _scope(module, other.bid_id, "Foreign scope")
    package = _package(module, valid_bid.bid_id, "PKG-1")
    foreign_package = _package(module, other.bid_id, "PKG-X")

    async def post(path: str, form: dict[str, str] | None = None):
        return await _request(module.app, "POST", path, form)

    scope_path = f"/requirements/{requirement.requirement_id}/scope-links"
    status, headers, _ = asyncio.run(post(scope_path, {"scope_item_id": scope.scope_item_id}))
    assert status == 303 and headers["location"].endswith("#coverage")
    successful_audit = _audit_count(module)
    for selected, expected in (
        (scope.scope_item_id, "already linked"),
        (foreign_scope.scope_item_id, "same Bid"),
    ):
        before_links = module.scope_repository.requirement_links(
            requirement_id=requirement.requirement_id
        )
        before_audit = _audit_count(module)
        status, headers, body = asyncio.run(post(scope_path, {"scope_item_id": selected}))
        assert status == 422 and headers["content-type"].startswith("text/html")
        assert expected.encode() in body and b'{"detail"' not in body and selected.encode() in body
        assert (
            module.scope_repository.requirement_links(requirement_id=requirement.requirement_id)
            == before_links
        )
        assert _audit_count(module) == before_audit
    before_audit = _audit_count(module)
    status, _, _ = asyncio.run(post(scope_path, {"scope_item_id": "SCOPE-MISSING"}))
    assert status == 404 and _audit_count(module) == before_audit
    unlink = f"{scope_path}/{scope.scope_item_id}/remove"
    assert asyncio.run(post(unlink))[0] == 303
    after_unlink_audit = _audit_count(module)
    assert asyncio.run(post(unlink))[0] == 404
    assert _audit_count(module) == after_unlink_audit
    assert successful_audit < after_unlink_audit

    package_path = f"/requirements/{requirement.requirement_id}/manufacturer-links"
    assert asyncio.run(post(package_path, {"package_id": package.package_id}))[0] == 303
    for selected, expected in (
        (package.package_id, "already linked"),
        (foreign_package.package_id, "same Bid"),
    ):
        before = module.ops07_repository.coverage(requirement.requirement_id)["packages"]
        before_audit = _audit_count(module)
        status, headers, body = asyncio.run(post(package_path, {"package_id": selected}))
        assert status == 422 and headers["content-type"].startswith("text/html")
        assert expected.encode() in body and b'{"detail"' not in body and selected.encode() in body
        assert module.ops07_repository.coverage(requirement.requirement_id)["packages"] == before
        assert _audit_count(module) == before_audit
    before_audit = _audit_count(module)
    assert asyncio.run(post(package_path, {"package_id": "PKG-MISSING"}))[0] == 404
    assert _audit_count(module) == before_audit
    unlink = f"{package_path}/{package.package_id}/remove"
    assert asyncio.run(post(unlink))[0] == 303
    after_unlink_audit = _audit_count(module)
    assert asyncio.run(post(unlink))[0] == 404
    assert _audit_count(module) == after_unlink_audit


def test_missing_requirement_link_routes_are_404(
    ops07_route_app: ModuleType,
) -> None:
    for suffix, form in (
        ("scope-links", {"scope_item_id": "SCOPE-MISSING"}),
        ("manufacturer-links", {"package_id": "PKG-MISSING"}),
    ):
        status, _, _ = asyncio.run(
            _request(ops07_route_app.app, "POST", f"/requirements/REQ-MISSING/{suffix}", form)
        )
        assert status == 404


def test_stale_scope_withdraw_retains_html_without_mutation_or_audit(
    ops07_route_app: ModuleType, valid_bid
) -> None:
    module = ops07_route_app
    module.bid_repository.create_bid(valid_bid)
    scope = _scope(module, valid_bid.bid_id, "Retained scope")
    before_audit = _audit_count(module)
    status, headers, body = asyncio.run(
        _request(
            module.app,
            "POST",
            f"/scope-items/{scope.scope_item_id}/withdraw",
            {"expected_version": "999"},
        )
    )
    assert status == 422
    assert headers["content-type"].startswith("text/html")
    assert b"stale" in body.lower() and b'{"detail"' not in body
    loaded = module.scope_repository.get_scope_item(scope.scope_item_id)
    assert loaded is not None and loaded.lifecycle_state.value == "ACTIVE"
    assert _audit_count(module) == before_audit


def test_contextual_scope_and_interface_selection_is_validated_and_rendered(
    ops07_route_app: ModuleType, valid_bid
) -> None:
    module = ops07_route_app
    module.bid_repository.create_bid(valid_bid)
    other = valid_bid.model_copy(update={"bid_id": "B-2026-9999"})
    module.bid_repository.create_bid(other)
    requirement = _requirement(module, valid_bid.bid_id, "Context requirement")
    foreign_requirement = _requirement(module, other.bid_id, "Foreign requirement")
    scope = _scope(module, valid_bid.bid_id, "Context scope")
    foreign_scope = _scope(module, other.bid_id, "Foreign scope")
    before_audit = _audit_count(module)

    status, _, body = asyncio.run(
        _request(
            module.app,
            "GET",
            f"/scope-interfaces?bid_id={valid_bid.bid_id}&requirement_id={requirement.requirement_id}",
        )
    )
    assert status == 200
    assert f'<option value="{requirement.requirement_id}" selected>'.encode() in body
    status, _, body = asyncio.run(
        _request(
            module.app,
            "GET",
            f"/scope-interfaces?bid_id={valid_bid.bid_id}&scope_item_id={scope.scope_item_id}",
        )
    )
    assert status == 200
    assert f'<option value="{scope.scope_item_id}" selected>'.encode() in body

    for query, expected in (
        ("requirement_id=REQ-MISSING", 404),
        (f"requirement_id={foreign_requirement.requirement_id}", 422),
        ("scope_item_id=SCOPE-MISSING", 404),
        (f"scope_item_id={foreign_scope.scope_item_id}", 422),
    ):
        response = asyncio.run(
            _request(
                module.app,
                "GET",
                f"/scope-interfaces?bid_id={valid_bid.bid_id}&{query}",
            )
        )
        assert response[0] == expected
        if expected == 422:
            assert response[1]["content-type"].startswith("text/html")
            assert b'{"detail"' not in response[2]
    assert _audit_count(module) == before_audit


def test_browser_gate_approval_is_audited_once_and_duplicate_is_atomic(
    ops07_route_app: ModuleType, valid_bid
) -> None:
    module = ops07_route_app
    module.bid_repository.create_bid(valid_bid)
    form = {
        "bid_id": valid_bid.bid_id,
        "approval_type": "bid_no_bid",
        "obtained": "on",
        "authority": "Director",
        "evidence_ref": "Minute",
        "decision": "Proceed",
    }
    status, _, _ = asyncio.run(_request(module.app, "POST", "/decisions/gate-approvals", form))
    assert status == 303
    approvals = module.bid_repository.list_approvals(valid_bid.bid_id)
    audits = [
        entry
        for entry in module.bid_repository.list_audit(valid_bid.bid_id)
        if entry.action == "bid_gate_approval_created"
    ]
    assert len(approvals) == len(audits) == 1
    status, headers, body = asyncio.run(
        _request(module.app, "POST", "/decisions/gate-approvals", form)
    )
    assert status == 422 and headers["content-type"].startswith("text/html")
    assert b"already been recorded" in body
    assert len(module.bid_repository.list_approvals(valid_bid.bid_id)) == 1
    assert (
        len(
            [
                entry
                for entry in module.bid_repository.list_audit(valid_bid.bid_id)
                if entry.action == "bid_gate_approval_created"
            ]
        )
        == 1
    )


def test_contextual_source_operation_is_durable_idempotent_and_bid_bound(
    ops07_route_app: ModuleType, valid_bid
) -> None:
    module = ops07_route_app
    module.bid_repository.create_bid(valid_bid)
    other = valid_bid.model_copy(update={"bid_id": "B-2026-9999"})
    module.bid_repository.create_bid(other)

    def operation_token(bid_id: str) -> str:
        status, _, page = asyncio.run(_request(module.app, "GET", f"/requirements?bid_id={bid_id}"))
        assert status == 200
        match = re.search(rb'name="source_operation_token" value="([^"]+)"', page)
        assert match is not None
        return match.group(1).decode()

    token = operation_token(valid_bid.bid_id)
    fields = {
        "bid_id": valid_bid.bid_id,
        "source_operation_token": token,
        "origin": "EXPLICIT",
        "category": "TECHNICAL",
        "significance": "MANDATORY",
        "lifecycle_stage": "BID",
        "title": "Arc flash",
        "statement": "Manufacturer shall confirm",
        "interpretation": "Written evidence required",
        "response_text": "Compliant subject to confirmation",
        "owner": "Jason",
        "contributor": "Engineering",
        "reviewer": "Commercial / Contracts",
        "source_clause": "1.4.2",
        "new_source_title": "Customer Electrical Specification",
        "new_source_version_label": "Revision 0",
    }
    body, content_type = _multipart(fields, "spec.pdf", b"%PDF idempotent")

    def counts() -> tuple[int, int, int, int]:
        with module.db._conn() as conn:
            return tuple(
                int(value)
                for value in conn.execute(
                    """SELECT
                    (SELECT count(*) FROM documents WHERE control_managed=1),
                    (SELECT count(*) FROM document_versions),
                    (SELECT count(*) FROM requirements),
                    (SELECT count(*) FROM audit_log WHERE action='controlled_document_created')"""
                ).fetchone()
            )  # type: ignore[return-value]

    first = asyncio.run(
        _request(
            module.app,
            "POST",
            "/requirements/with-source",
            body=body,
            content_type=content_type,
        )
    )
    assert first[0] == 303
    assert counts() == (1, 1, 0, 1)
    first_form = asyncio.run(_request(module.app, "GET", first[1]["location"]))[2]
    assert b"Manufacturer shall confirm" in first_form
    assert b"Commercial / Contracts" in first_form
    assert b" selected>Customer Electrical Specification" in first_form

    replay = asyncio.run(
        _request(
            module.app,
            "POST",
            "/requirements/with-source",
            body=body,
            content_type=content_type,
        )
    )
    assert replay[0] == 303
    assert counts() == (1, 1, 0, 1)
    replay_form = asyncio.run(_request(module.app, "GET", replay[1]["location"]))[2]
    assert b"Manufacturer shall confirm" in replay_form
    assert b" selected>Customer Electrical Specification" in replay_form

    tampered = dict(fields)
    tampered["source_operation_token"] = token[:-1] + ("A" if token[-1] != "A" else "B")
    tampered_body, tampered_type = _multipart(tampered, "spec.pdf", b"%PDF idempotent")
    rejected = asyncio.run(
        _request(
            module.app,
            "POST",
            "/requirements/with-source",
            body=tampered_body,
            content_type=tampered_type,
        )
    )
    assert rejected[0] == 422 and b"invalid for this Bid" in rejected[2]
    assert counts() == (1, 1, 0, 1)

    cross_bid = dict(fields, bid_id=other.bid_id)
    cross_body, cross_type = _multipart(cross_bid, "spec.pdf", b"%PDF idempotent")
    rejected = asyncio.run(
        _request(
            module.app,
            "POST",
            "/requirements/with-source",
            body=cross_body,
            content_type=cross_type,
        )
    )
    assert rejected[0] == 422 and b"invalid for this Bid" in rejected[2]
    assert counts() == (1, 1, 0, 1)

    failure_fields = dict(fields, source_operation_token=operation_token(valid_bid.bid_id))
    failure_body, failure_type = _multipart(failure_fields, "fault.pdf", b"%PDF fault")
    with module.db._conn() as conn:
        conn.execute(
            """CREATE TRIGGER reject_source_audit BEFORE INSERT ON audit_log
            WHEN NEW.action='controlled_document_created'
            BEGIN SELECT RAISE(ABORT,'injected source audit failure'); END"""
        )
    failed = asyncio.run(
        _request(
            module.app,
            "POST",
            "/requirements/with-source",
            body=failure_body,
            content_type=failure_type,
        )
    )
    assert failed[0] == 422 and b"injected source audit failure" in failed[2]
    assert counts() == (1, 1, 0, 1)
    with module.db._conn() as conn:
        conn.execute("DROP TRIGGER reject_source_audit")

    concurrent_fields = dict(fields, source_operation_token=operation_token(valid_bid.bid_id))
    concurrent_body, concurrent_type = _multipart(
        concurrent_fields, "concurrent.pdf", b"%PDF concurrent"
    )

    def submit() -> tuple[int, dict[str, str], bytes]:
        return asyncio.run(
            _request(
                module.app,
                "POST",
                "/requirements/with-source",
                body=concurrent_body,
                content_type=concurrent_type,
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent_results = list(executor.map(lambda _index: submit(), range(2)))
    assert [result[0] for result in concurrent_results] == [303, 303]
    assert counts() == (2, 2, 0, 2)

    new_fields = dict(fields, source_operation_token=operation_token(valid_bid.bid_id))
    new_body, new_type = _multipart(new_fields, "spec.pdf", b"%PDF idempotent")
    new_result = asyncio.run(
        _request(
            module.app,
            "POST",
            "/requirements/with-source",
            body=new_body,
            content_type=new_type,
        )
    )
    assert new_result[0] == 303
    assert counts() == (3, 3, 0, 3)


def test_multipart_source_fields_are_partitioned_before_final_requirement_create(
    ops07_route_app: ModuleType, valid_bid
) -> None:
    module = ops07_route_app
    module.bid_repository.create_bid(valid_bid)
    page = asyncio.run(_request(module.app, "GET", f"/requirements?bid_id={valid_bid.bid_id}"))[2]
    token_match = re.search(rb'name="source_operation_token" value="([^"]+)"', page)
    assert token_match is not None
    fields = {
        "bid_id": valid_bid.bid_id,
        "source_operation_token": token_match.group(1).decode(),
        "origin": "EXPLICIT",
        "category": "SCHEDULE",
        "significance": "DISQUALIFYING",
        "lifecycle_stage": "BID",
        "title": "MCC drawing timing",
        "statement": "Customer requires MCC drawings within four weeks of purchase order.",
        "interpretation": "Written manufacturer evidence is needed.",
        "response_text": "Comply, subject to manufacturer confirmation.",
        "disposition": "COMPLY",
        "work_state": "OPEN",
        "owner": "Jason",
        "contributor": "Engineering",
        "reviewer": "Commercial / Contracts",
        "due_date": "2026-10-01",
        "source_clause": "Section 1.4.2",
        "new_source_title": "Customer Electrical Specification",
        "new_source_version_label": "Revision 0",
    }
    body, content_type = _multipart(fields, "customer-spec.pdf", b"%PDF browser boundary")
    registered = asyncio.run(
        _request(
            module.app,
            "POST",
            "/requirements/with-source",
            body=body,
            content_type=content_type,
        )
    )
    assert registered[0] == 303
    returned = asyncio.run(_request(module.app, "GET", registered[1]["location"]))[2]
    assert b"Customer source registered" in returned
    for retained in (
        b"MCC drawing timing",
        b"Customer requires MCC drawings within four weeks",
        b"Comply, subject to manufacturer confirmation",
        b"Jason",
        b"Engineering",
        b"Commercial / Contracts",
    ):
        assert retained in returned
    version_match = re.search(
        rb'<option value="([^"]+)" selected>Customer Electrical Specification', returned
    )
    assert version_match is not None
    with module.db._conn() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM requirements WHERE bid_id=?", (valid_bid.bid_id,)
            ).fetchone()[0]
            == 0
        )

    final_fields = dict(fields)
    final_fields["source_document_version_id"] = version_match.group(1).decode()
    final_body, final_type = _multipart(final_fields, "", b"")
    created = asyncio.run(
        _request(
            module.app,
            "POST",
            "/requirements",
            body=final_body,
            content_type=final_type,
        )
    )
    assert created[0] == 303
    requirements = module.requirement_service.list_requirements(
        bid_id=valid_bid.bid_id, as_of_date=valid_bid.internal_due_date
    )
    assert len(requirements) == 1
    assert requirements[0].statement == fields["statement"]
    assert requirements[0].response_text == fields["response_text"]
    assert requirements[0].source_document_version_id == version_match.group(1).decode()
    assert "Extra inputs are not permitted" not in created[2].decode()
    with module.db._conn() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM audit_log WHERE action='requirement_created'"
            ).fetchone()[0]
            == 1
        )

    before = len(module.requirement_service.audit_history(requirements[0].requirement_id))
    rejected_fields = dict(final_fields, unexpected_browser_field="tampered")
    rejected_body, rejected_type = _multipart(rejected_fields, "", b"")
    rejected = asyncio.run(
        _request(
            module.app,
            "POST",
            "/requirements",
            body=rejected_body,
            content_type=rejected_type,
        )
    )
    assert rejected[0] == 422
    assert rejected[1]["content-type"].startswith("text/html")
    assert b"unexpected_browser_field" in rejected[2]
    assert (
        len(
            module.requirement_service.list_requirements(
                bid_id=valid_bid.bid_id, as_of_date=valid_bid.internal_due_date
            )
        )
        == 1
    )
    assert len(module.requirement_service.audit_history(requirements[0].requirement_id)) == before
