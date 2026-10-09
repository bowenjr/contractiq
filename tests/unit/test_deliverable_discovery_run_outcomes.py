"""Run-outcome tests mirroring ``test_deliverable_discovery_service.py`` fixtures."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.bid_package_intake import (
    AnalysisEligibility,
    ClassificationMethod,
    ContentForm,
    FileDispositionCreate,
)
from core.deliverable_discovery import DiscoveryRun, RunStatus
from core.deliverable_discovery_repository import DeliverableDiscoveryRepository
from core.deliverable_discovery_service import DeliverableDiscoveryService
from core.deliverable_repository import DeliverableRepository
from core.deliverable_service import DeliverableService
from core.document_repository import DocumentRepository
from core.document_service import DocumentService
from core.managed_document_storage import ManagedDocumentStorage
from tests.unit.test_bid_package_storage import BID_ID, _command, _service
from tests.unit.test_deliverable_discovery_service import _pdf_bytes


class FakeClient:
    def __init__(self, mode: str = "normal") -> None:
        self.mode = mode
        self.calls = 0

    def complete_json(self, prompt: str, **_kwargs: object) -> object:
        self.calls += 1
        if self.mode == "unreachable" or "MODEL FAIL" in prompt:
            raise ConnectionError("synthetic model endpoint unavailable")
        if self.mode == "invalid" or "INVALID OUTPUT" in prompt:
            return {"candidates": "not-a-list"}
        if self.mode == "zero":
            return {"candidates": []}
        if self.mode == "bad-candidate":
            return {"candidates": [{"title": "", "category": "FORM"}]}
        return {
            "candidates": [
                {
                    "title": "Signed Bid Form",
                    "category": "FORM",
                    "criticality": "REQUIRED",
                    "condition_text": None,
                    "due_date": None,
                    "requested_format": "PDF",
                    "requested_recipient": "Procurement",
                    "proposed_owner": None,
                }
            ]
        }


def _fixture(tmp_path: Path, files: dict[str, bytes], client: FakeClient) -> dict[str, object]:
    package, package_repository, database, source, _managed = _service(tmp_path)
    for filename, content in files.items():
        (source / filename).write_bytes(content)
    preview = package.preview(BID_ID, "landing")
    release = package.register_release(_command(preview.source_fingerprint), "Jason")
    release_id = str(release["release_id"])
    rows = package.release_detail(BID_ID, release_id)["files"]
    for index, row in enumerate(rows):
        package.classify_file(
            BID_ID,
            str(row["file_id"]),
            FileDispositionCreate(
                content_form=ContentForm.TEXTUAL,
                classification_method=ClassificationMethod.HUMAN_REVIEW,
                confidence=1,
                analysis_eligibility=AnalysisEligibility.ELIGIBLE,
                supersedes_event_id=str(row["disposition_event_id"]),
                operation_id=f"include-{index}",
            ),
            "Jason",
        )
    documents = DocumentRepository(database)
    repository = DeliverableDiscoveryRepository(database)
    deliverables = DeliverableRepository(database)
    service = DeliverableDiscoveryService(
        repository,
        package_repository,
        DocumentService(
            documents,
            package.bid_repository,
            ManagedDocumentStorage(tmp_path / "documents", 10_000_000),
        ),
        deliverables,
        DeliverableService(deliverables),
        client,
    )
    return {
        "service": service,
        "repository": repository,
        "database": database,
        "client": client,
    }


def _outcomes(fx: dict[str, object], run_id: str) -> list[sqlite3.Row]:
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        return conn.execute(
            "SELECT * FROM deliverable_discovery_file_outcomes WHERE run_id=? ORDER BY outcome",
            (run_id,),
        ).fetchall()


def test_all_unsupported_is_failed_without_model_calls(tmp_path: Path) -> None:
    fx = _fixture(tmp_path, {"notes.txt": b"notes"}, FakeClient())
    result = fx["service"].run_discovery(BID_ID, "unsupported", "Jason")  # type: ignore[union-attr]
    assert (result["status"], result["outcome"], result["model_calls"]) == (
        "FAILED",
        "FAILED",
        0,
    )
    assert fx["client"].calls == 0  # type: ignore[union-attr]
    assert [row["outcome"] for row in _outcomes(fx, result["run_id"])] == ["UNSUPPORTED_TYPE"]


def test_all_model_unreachable_is_failed_not_invalid(tmp_path: Path) -> None:
    fx = _fixture(
        tmp_path,
        {"bid.pdf": _pdf_bytes(["MODEL FAIL for this complete readable customer Bid passage now"])},
        FakeClient(),
    )
    result = fx["service"].run_discovery(BID_ID, "offline", "Jason")  # type: ignore[union-attr]
    assert result["outcome"] == "FAILED"
    assert result["rejected_source_count"] == 0
    assert result["passages_model_failed"] == 1
    row = _outcomes(fx, result["run_id"])[0]
    assert row["outcome"] == "MODEL_UNAVAILABLE"
    assert "ConnectionError: synthetic model endpoint unavailable" in row["detail"]


def test_mixed_files_are_partial_and_every_file_is_recorded(tmp_path: Path) -> None:
    files = {
        "good.pdf": _pdf_bytes(
            ["Submit the completed signed Bid Form to Procurement with the complete offer now."]
        ),
        "offline.pdf": _pdf_bytes(
            ["MODEL FAIL for this complete readable customer Bid passage now"]
        ),
        "notes.txt": b"unsupported",
    }
    fx = _fixture(tmp_path, files, FakeClient())
    result = fx["service"].run_discovery(BID_ID, "mixed", "Jason")  # type: ignore[union-attr]
    assert (result["status"], result["outcome"], result["files_included"]) == (
        "SUCCEEDED",
        "PARTIAL",
        3,
    )
    assert {row["outcome"] for row in _outcomes(fx, result["run_id"])} == {
        "ANALYSED_CANDIDATES",
        "MODEL_UNAVAILABLE",
        "UNSUPPORTED_TYPE",
    }


@pytest.mark.parametrize(
    ("mode", "expected_outcome", "rejected"),
    (("zero", "ANALYSED_ZERO_CANDIDATES", 0), ("normal", "ANALYSED_CANDIDATES", 0)),
)
def test_successful_analysis_including_zero_candidate_finding(
    tmp_path: Path, mode: str, expected_outcome: str, rejected: int
) -> None:
    fx = _fixture(
        tmp_path,
        {
            "bid.pdf": _pdf_bytes(
                ["Submit the completed signed Bid Form to Procurement with the complete offer now."]
            )
        },
        FakeClient(mode),
    )
    result = fx["service"].run_discovery(BID_ID, f"success-{mode}", "Jason")  # type: ignore[union-attr]
    assert result["outcome"] == "SUCCEEDED"
    assert result["rejected_source_count"] == rejected
    assert _outcomes(fx, result["run_id"])[0]["outcome"] == expected_outcome
    proposals = fx["service"].list_review_queue(BID_ID)  # type: ignore[union-attr]
    assert len(proposals) == (mode == "normal")
    if proposals:
        assert proposals[0]["sources"][0]["received_file_id"]


@pytest.mark.parametrize("mode", ("invalid", "bad-candidate"))
def test_invalid_reply_and_candidate_are_invalid_not_transport(tmp_path: Path, mode: str) -> None:
    passage = (
        "INVALID OUTPUT from this complete readable customer Bid passage right now"
        if mode == "invalid"
        else "This ordinary complete readable customer Bid passage has enough words for analysis"
    )
    fx = _fixture(tmp_path, {"bid.pdf": _pdf_bytes([passage])}, FakeClient(mode))
    result = fx["service"].run_discovery(BID_ID, f"invalid-{mode}", "Jason")  # type: ignore[union-attr]
    assert result["rejected_source_count"] == 1
    assert result["passages_model_failed"] == 0
    if mode == "invalid":
        assert result["outcome"] == "FAILED"
        assert _outcomes(fx, result["run_id"])[0]["outcome"] == "INVALID_MODEL_OUTPUT"
    else:
        assert result["outcome"] == "SUCCEEDED"
        assert result["passages_analysed"] == 1


def test_replay_and_retry_preserve_failed_run(tmp_path: Path) -> None:
    client = FakeClient("unreachable")
    fx = _fixture(
        tmp_path,
        {
            "bid.pdf": _pdf_bytes(
                ["This complete readable customer Bid passage has enough words now"]
            )
        },
        client,
    )
    first = fx["service"].run_discovery(BID_ID, "same", "Jason")  # type: ignore[union-attr]
    calls = client.calls
    replay = fx["service"].run_discovery(BID_ID, "same", "Jason")  # type: ignore[union-attr]
    assert replay["run_id"] == first["run_id"]
    assert client.calls == calls
    client.mode = "normal"
    retry = fx["service"].run_discovery(BID_ID, "retry", "Jason")  # type: ignore[union-attr]
    assert retry["outcome"] == "SUCCEEDED"
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        assert conn.execute("SELECT count(*) FROM deliverable_discovery_runs").fetchone()[0] == 2
        assert (
            conn.execute("SELECT count(*) FROM deliverable_discovery_run_outcomes").fetchone()[0]
            == 2
        )


def test_migration_preserves_legacy_run_and_outcomes_are_append_only(tmp_path: Path) -> None:
    fx = _fixture(tmp_path, {"notes.txt": b"notes"}, FakeClient())
    now = datetime.now(UTC)
    legacy = DiscoveryRun(
        bid_id=BID_ID,
        operation_id="legacy",
        status=RunStatus.SUCCEEDED,
        proposal_count=0,
        actor="Jason",
        started_at=now,
        completed_at=now,
    )
    fx["repository"].create_run(legacy, [], [], "Jason")  # type: ignore[union-attr]
    fx["repository"]._migrate()  # type: ignore[union-attr]
    assert fx["repository"].existing_run(BID_ID, "legacy")["outcome"] is None  # type: ignore[union-attr,index]
    failed = fx["service"].run_discovery(BID_ID, "new", "Jason")  # type: ignore[union-attr]
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE deliverable_discovery_run_outcomes SET outcome='SUCCEEDED' WHERE run_id=?",
                (failed["run_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute(
                "DELETE FROM deliverable_discovery_file_outcomes WHERE run_id=?",
                (failed["run_id"],),
            )
