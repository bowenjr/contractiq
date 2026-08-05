import asyncio
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.datastructures import Headers

from core.schemas import Bid


class JsonRequest:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def json(self) -> dict[str, object]:
        return dict(self.payload)


def _upload(content: bytes, filename: str = "Synthetic RFP.txt") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": "text/plain"}),
    )


def _json(response: JSONResponse) -> dict[str, object]:
    return cast(dict[str, object], json.loads(bytes(response.body)))


def _html(response: HTMLResponse) -> str:
    return bytes(response.body).decode()


async def _stream_bytes(response: StreamingResponse) -> bytes:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    return b"".join(chunks)


@pytest.fixture
def document_ui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setenv("CONTRACTIQ_DOCUMENT_ROOT", str(tmp_path / "managed"))
    sys.modules.pop("app", None)
    app_path = Path(__file__).parents[2] / "app.py"
    spec = importlib.util.spec_from_file_location("app", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load app.py for controlled-document UI tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    return module


def _register(module: ModuleType, bid: Bid, content: bytes = b"synthetic-v1") -> dict[str, object]:
    response = asyncio.run(
        module.register_controlled_document(
            file=_upload(content, "../../Synthetic RFP.txt"),
            bid_id=bid.bid_id,
            title="Synthetic RFP",
            category="SOLICITATION",
            version_label="Original",
            document_number="RFP-001",
            issuer="Example EPC",
            notes="Synthetic evidence only",
            issued_date="2026-08-01",
            received_at=None,
            actor="jason",
        )
    )
    assert response.status_code == 201
    return _json(response)


def test_documents_empty_page_and_navigation_are_available(
    document_ui_app: ModuleType,
) -> None:
    def fail_if_alice_is_contacted() -> None:
        raise AssertionError("Documents/Dashboard/My Day must not contact Alice")

    document_ui_app.llm_client.health_check = fail_if_alice_is_contacted
    page = asyncio.run(
        document_ui_app.controlled_documents(cast(Request, object()), None, None, None)
    )
    dashboard = asyncio.run(document_ui_app.index(cast(Request, object())))
    my_day = asyncio.run(document_ui_app.my_day(cast(Request, object()), "2026-08-05"))
    assert page.status_code == dashboard.status_code == my_day.status_code == 200
    assert "No controlled documents yet" in _html(page)
    assert "ContractIQ" in _html(page)
    assert 'href="/documents"' in _html(dashboard)
    assert 'href="/documents"' in _html(my_day)


def test_ui_registers_displays_versions_downloads_and_verifies(
    document_ui_app: ModuleType,
    valid_bid: Bid,
) -> None:
    document_ui_app.bid_repository.create_bid(valid_bid)
    created = _register(document_ui_app, valid_bid)
    document = cast(dict[str, object], created["document"])
    first = cast(dict[str, object], created["version"])
    document_id = str(document["document_id"])

    register_page = asyncio.run(
        document_ui_app.controlled_documents(cast(Request, object()), None, None, None)
    )
    page_text = _html(register_page)
    assert "Synthetic RFP" in page_text
    assert valid_bid.project_name in page_text
    assert "Integrity: not checked in this view" in page_text
    assert str(document_ui_app.MANAGED_DOCUMENT_ROOT) not in page_text

    added_response = asyncio.run(
        document_ui_app.add_controlled_document_version(
            document_id=document_id,
            file=_upload(b"synthetic-v2", "Addendum 1.txt"),
            version_label="Addendum 1 incorporated",
            expected_document_version=int(document["version"]),
            expected_current_version_id=str(first["document_version_id"]),
            issued_date="2026-08-03",
            received_at=None,
            actor="jason",
        )
    )
    added = _json(added_response)
    second = cast(dict[str, object], added["version"])
    detail = asyncio.run(
        document_ui_app.controlled_document_detail(
            cast(Request, object()), document_id, str(second["document_version_id"])
        )
    )
    detail_text = _html(detail)
    assert "Addendum 1 incorporated" in detail_text
    assert "CURRENT" in detail_text and "SUPERSEDED" in detail_text
    assert "OK" in detail_text
    assert str(document_ui_app.MANAGED_DOCUMENT_ROOT) not in detail_text

    download = asyncio.run(
        document_ui_app.download_controlled_document_version(str(second["document_version_id"]))
    )
    assert asyncio.run(_stream_bytes(download)) == b"synthetic-v2"
    disposition = download.headers["content-disposition"]
    assert "Addendum%201.txt" in disposition
    assert str(document_ui_app.MANAGED_DOCUMENT_ROOT) not in disposition


def test_ui_filters_edits_withdraws_and_preserves_access(
    document_ui_app: ModuleType,
    valid_bid: Bid,
) -> None:
    document_ui_app.bid_repository.create_bid(valid_bid)
    created = _register(document_ui_app, valid_bid)
    document = cast(dict[str, object], created["document"])
    document_id = str(document["document_id"])
    edit = asyncio.run(
        document_ui_app.edit_controlled_document(
            document_id,
            cast(
                Request,
                JsonRequest(
                    {
                        "expected_version": document["version"],
                        "title": "Synthetic RFP controlled",
                        "category": "CONTRACTUAL",
                        "actor": "jason",
                    }
                ),
            ),
        )
    )
    edited = _json(edit)
    withdrawn_response = asyncio.run(
        document_ui_app.withdraw_controlled_document(
            document_id,
            cast(
                Request,
                JsonRequest({"expected_version": edited["version"], "actor": "jason"}),
            ),
        )
    )
    assert _json(withdrawn_response)["lifecycle_state"] == "WITHDRAWN"
    page = asyncio.run(
        document_ui_app.controlled_documents(
            cast(Request, object()), valid_bid.bid_id, "CONTRACTUAL", "WITHDRAWN"
        )
    )
    assert "Synthetic RFP controlled" in _html(page)
    detail = asyncio.run(
        document_ui_app.controlled_document_detail(cast(Request, object()), document_id, None)
    )
    assert detail.status_code == 200
    with pytest.raises(HTTPException) as deletion:
        asyncio.run(document_ui_app.delete_document(document_id))
    assert deletion.value.status_code == 405


def test_ui_validation_errors_leave_no_file_audit_or_document(
    document_ui_app: ModuleType,
    valid_bid: Bid,
) -> None:
    document_ui_app.bid_repository.create_bid(valid_bid)
    with pytest.raises(HTTPException) as invalid:
        asyncio.run(
            document_ui_app.register_controlled_document(
                file=_upload(b"bytes"),
                bid_id=valid_bid.bid_id,
                title="   ",
                category="SOLICITATION",
                version_label="Original",
                document_number=None,
                issuer=None,
                notes=None,
                issued_date=None,
                received_at=None,
                actor="jason",
            )
        )
    assert invalid.value.status_code == 422
    assert document_ui_app.document_repository.list_documents() == []
    assert document_ui_app.bid_repository.list_audit(valid_bid.bid_id) == []
    assert document_ui_app.managed_document_storage.staging_files() == []
    assert list(document_ui_app.managed_document_storage.iter_managed_keys()) == []
