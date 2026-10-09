from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest

from core.bid_package_intake import (
    AnalysisEligibility,
    ClassificationMethod,
    ContentForm,
    FileDispositionCreate,
)
from core.deliverable_discovery_extraction import extract_units
from core.deliverable_discovery_repository import DeliverableDiscoveryRepository
from core.deliverable_discovery_service import DeliverableDiscoveryService
from core.deliverable_repository import DeliverableRepository
from core.deliverable_service import DeliverableService
from core.document_repository import DocumentRepository
from core.document_service import DocumentService
from core.managed_document_storage import ManagedDocumentStorage
from scripts.asgi_acceptance_ops08 import request
from tests.unit.test_bid_package_storage import BID_ID, _command, _service
from tests.unit.test_deliverable_discovery_extraction import _docx_bytes
from tests.unit.test_deliverable_discovery_service import _pdf_bytes, _StubLLM


@pytest.mark.parametrize(
    ("filename", "locator_type"),
    (
        ("corrupt.pdf", "PAGE"),
        ("corrupt.docx", "PARAGRAPH"),
        ("corrupt.xlsx", "SHEET_CELL"),
    ),
)
def test_corrupt_supported_files_return_one_unreadable_unit(
    filename: str, locator_type: str
) -> None:
    units = extract_units(filename, None, io.BytesIO(b"not really this file type"))

    assert len(units) == 1
    assert units[0].locator_type == locator_type
    assert units[0].locator == "file"
    assert units[0].text == ""
    assert units[0].readable is False
    assert units[0].failure_reason is not None
    assert "could not be parsed" in units[0].failure_reason
    assert "re-obtain the file" in units[0].failure_reason


def test_midway_parser_failure_keeps_prior_units(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fitz

    class _Page:
        def __init__(self, text: str | None) -> None:
            self.text = text

        def get_text(self, _mode: str) -> str:
            if self.text is None:
                raise RuntimeError("synthetic later-page failure")
            return self.text

    class _Document:
        def __iter__(self):
            return iter(
                (
                    _Page("Submit the signed Bid Form with the complete offer package today."),
                    _Page(None),
                )
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr(fitz, "open", lambda **_kwargs: _Document())
    units = extract_units("partial.pdf", "application/pdf", io.BytesIO(b"synthetic"))

    assert [unit.locator for unit in units] == ["p.1", "file"]
    assert units[0].readable is True
    assert units[1].readable is False
    assert "RuntimeError" in str(units[1].failure_reason)


def _discovery_fixture(
    tmp_path: Path,
    *,
    pdf_content: bytes | None = None,
    docx_content: bytes | None = None,
) -> dict[str, object]:
    package, package_repository, database, source, _managed = _service(tmp_path)
    (source / "corrupt.pdf").write_bytes(
        pdf_content if pdf_content is not None else b"corrupt pdf bytes"
    )
    (source / "good.docx").write_bytes(
        docx_content
        if docx_content is not None
        else _docx_bytes(
            ["A complete technical schedule must accompany every submission to procurement."]
        )
    )
    preview = package.preview(BID_ID, "landing")
    release = package.register_release(_command(preview.source_fingerprint), "Jason")
    release_id = str(release["release_id"])
    rows = {
        str(row["original_filename"]): row
        for row in package.release_detail(BID_ID, release_id)["files"]
    }
    for index, row in enumerate(rows.values()):
        package.classify_file(
            BID_ID,
            str(row["file_id"]),
            FileDispositionCreate(
                content_form=ContentForm.TEXTUAL,
                classification_method=ClassificationMethod.HUMAN_REVIEW,
                confidence=1,
                analysis_eligibility=AnalysisEligibility.ELIGIBLE,
                supersedes_event_id=str(row["disposition_event_id"]),
                operation_id=f"eligible-{index}",
            ),
            "Jason",
        )

    documents = DocumentRepository(database)
    discovery_repository = DeliverableDiscoveryRepository(database)
    deliverables = DeliverableRepository(database)
    discovery = DeliverableDiscoveryService(
        discovery_repository,
        package_repository,
        DocumentService(
            documents,
            package.bid_repository,
            ManagedDocumentStorage(tmp_path / "managed-documents", 10_000_000),
        ),
        deliverables,
        DeliverableService(deliverables),
        _StubLLM(),
    )
    return {
        "package": package,
        "package_repository": package_repository,
        "database": database,
        "discovery_repository": discovery_repository,
        "discovery": discovery,
        "release_id": release_id,
        "rows": rows,
    }


def test_mixed_corrupt_and_good_run_succeeds_with_proposal_and_unreadable_row(
    tmp_path: Path,
) -> None:
    fx = _discovery_fixture(tmp_path)

    result = fx["discovery"].run_discovery(BID_ID, "mixed-run", "Jason")

    assert result["status"] == "SUCCEEDED"
    assert result["outcome"] == "PARTIAL"
    assert result["proposal_count"] == 1
    assert [row["title"] for row in fx["discovery"].list_review_queue(BID_ID)] == [
        "Technical Schedule"
    ]
    unreadable = fx["discovery_repository"].list_unreadable_extractions(BID_ID)
    assert len(unreadable) == 1
    assert unreadable[0]["original_filename"] == "corrupt.pdf"
    assert unreadable[0]["locator"] == "file"
    assert "could not be parsed as PDF" in unreadable[0]["failure_reason"]
    with fx["database"]._conn() as conn:
        row = conn.execute(
            "SELECT status,text_excerpt FROM deliverable_discovery_extractions_v2 "
            "WHERE received_file_id=?",
            (str(fx["rows"]["corrupt.pdf"]["file_id"]),),
        ).fetchone()
    assert (row["status"], row["text_excerpt"]) == ("UNREADABLE", "")


def test_open_failure_is_unreadable_and_newer_success_clears_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _discovery_fixture(
        tmp_path,
        pdf_content=_pdf_bytes(
            ["Submit the completed signed Bid Form with the offer before the deadline please."]
        ),
    )
    repository = fx["package_repository"]
    original_open = repository.open_received_file
    failed_file_id = str(fx["rows"]["corrupt.pdf"]["file_id"])

    def fail_one_open(bid_id: str, release_id: str, file_id: str):
        if file_id == failed_file_id:
            raise OSError("synthetic managed-copy failure")
        return original_open(bid_id, release_id, file_id)

    monkeypatch.setattr(repository, "open_received_file", fail_one_open)
    first = fx["discovery"].run_discovery(BID_ID, "open-failure", "Jason")
    assert first["status"] == "SUCCEEDED"
    unreadable = fx["discovery_repository"].list_unreadable_extractions(BID_ID)
    assert len(unreadable) == 1
    assert unreadable[0]["received_file_id"] == failed_file_id
    assert "could not be opened or verified" in unreadable[0]["failure_reason"]
    assert "re-register the file" in unreadable[0]["failure_reason"]

    monkeypatch.setattr(repository, "open_received_file", original_open)
    second = fx["discovery"].run_discovery(BID_ID, "open-fixed", "Jason")
    assert second["status"] == "SUCCEEDED"
    assert fx["discovery_repository"].list_unreadable_extractions(BID_ID) == []


def test_handle_read_failure_is_recorded_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _discovery_fixture(
        tmp_path,
        pdf_content=_pdf_bytes(
            ["Submit the completed signed Bid Form with the offer before the deadline please."]
        ),
    )
    repository = fx["package_repository"]
    original_open = repository.open_received_file
    failed_file_id = str(fx["rows"]["corrupt.pdf"]["file_id"])

    class _ReadFailure(io.BytesIO):
        def read(self, _size: int = -1) -> bytes:
            raise OSError("synthetic handle read failure")

    def fail_one_read(bid_id: str, release_id: str, file_id: str):
        handle, evidence = original_open(bid_id, release_id, file_id)
        if file_id != failed_file_id:
            return handle, evidence
        handle.close()
        return _ReadFailure(), evidence

    monkeypatch.setattr(repository, "open_received_file", fail_one_read)
    result = fx["discovery"].run_discovery(BID_ID, "read-failure", "Jason")

    assert result["status"] == "SUCCEEDED"
    unreadable = fx["discovery_repository"].list_unreadable_extractions(BID_ID)
    assert len(unreadable) == 1
    assert unreadable[0]["received_file_id"] == failed_file_id
    assert "could not be opened or verified" in unreadable[0]["failure_reason"]


def test_all_corrupt_files_fail_with_zero_proposals_and_unreadable_state(
    tmp_path: Path,
) -> None:
    fx = _discovery_fixture(tmp_path, docx_content=b"also corrupt docx bytes")

    result = fx["discovery"].run_discovery(BID_ID, "all-corrupt", "Jason")

    assert result["status"] == "FAILED"
    assert result["outcome"] == "FAILED"
    assert result["proposal_count"] == 0
    assert fx["discovery"].list_review_queue(BID_ID) == []
    unreadable = fx["discovery_repository"].list_unreadable_extractions(BID_ID)
    assert [row["original_filename"] for row in unreadable] == [
        "corrupt.pdf",
        "good.docx",
    ]


def test_discovery_page_renders_unreadable_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app as app_module

    fx = _discovery_fixture(tmp_path)
    fx["discovery"].run_discovery(BID_ID, "route-run", "Jason")
    monkeypatch.setattr(app_module, "bid_repository", fx["package"].bid_repository)
    monkeypatch.setattr(app_module, "deliverable_discovery_service", fx["discovery"])
    monkeypatch.setattr(app_module, "deliverable_discovery_repository", fx["discovery_repository"])

    status, _, body = asyncio.run(
        request(app_module.app, "GET", f"/bids/{BID_ID}/deliverable-discovery")
    )

    assert status == 200
    assert b"Files that could not be read" in body
    assert b"corrupt.pdf" in body
    assert b"These files were NOT analysed" in body
    assert b"does not mean they contain no deliverables" in body
