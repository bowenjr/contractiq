"""Coverage UI scenarios mirroring the fixture pattern in
``test_deliverable_discovery_e2e_workflow.py`` with synthetic files and a fake local LLM.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlsplit

import pytest

from core.deliverable_discovery import DiscoveryRun, RunStatus
from scripts.asgi_acceptance_ops08 import request
from tests.unit.deliverable_discovery_job_route_helpers import post_run_and_finish
from tests.unit.test_bid_package_governing_ui import (
    BID_ID,
    _files_by_name,
    _register_release,
    _review,
)
from tests.unit.test_bid_package_governing_ui import (
    governing_ui_app as _governing_ui_app,
)
from tests.unit.test_deliverable_discovery_service import _pdf_bytes


@pytest.fixture
def coverage_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    yield from _governing_ui_app.__wrapped__(tmp_path, monkeypatch)


def _source(app: ModuleType) -> Path:
    return next(iter(app.INTAKE_SOURCE_ROOTS.values()))


def _make_folder(app: ModuleType, name: str, files: dict[str, bytes]) -> None:
    folder = _source(app) / name
    folder.mkdir()
    for filename, content in files.items():
        (folder / filename).write_bytes(content)


def _included_pdf() -> bytes:
    return _pdf_bytes(
        [
            "The supplier must submit the signed Bid Form with the complete offer to "
            "Procurement before the customer submission deadline."
        ]
    )


async def _register_raw(app: ModuleType, folder: str) -> str:
    root = f"/bids/{BID_ID}/package-intake-addenda"
    status, _, preview = await request(
        app.app,
        "POST",
        f"{root}/preview",
        {
            "release_type": "INITIAL_PACKAGE",
            "source_root_alias": "inbox_1",
            "source_folder": folder,
            "intake_mode": "initial",
            "exact_customer_reference": folder,
            "received_channel": "EMAIL",
        },
    )
    assert status == 200
    token = re.search(rb'name="confirmation_token" value="([^"]+)"', preview)
    assert token is not None
    status, headers, _ = await request(
        app.app,
        "POST",
        f"{root}/register",
        {"confirmation_token": token.group(1).decode()},
    )
    assert status == 303
    return headers["location"]


async def _partial_package(app: ModuleType, folder: str) -> str:
    _make_folder(
        app,
        folder,
        {
            "included.pdf": _included_pdf(),
            "pending-one.pdf": _included_pdf(),
            "pending-two.pdf": _included_pdf(),
        },
    )
    release_path = await _register_release(app, folder=folder, reference=folder)
    release_id = release_path.rsplit("/", 1)[-1]
    files = _files_by_name(app, BID_ID, release_id)
    await _review(
        app,
        release_path,
        [str(files["included.pdf"]["file_id"])],
        "ELIGIBLE",
        f"include-{folder}",
    )
    return release_path


def test_partial_review_offers_discovery_without_counting_undecided_files(
    coverage_app: ModuleType,
) -> None:
    async def scenario() -> None:
        path = await _partial_package(coverage_app, "Partial-Rail")
        status, _, page = await request(coverage_app.app, "GET", path)
        assert status == 200
        assert b">Run deliverables discovery</a>" in page
        assert b"1 file(s) are Included for analysis" in page
        assert (
            b"2 file(s) are still undecided and will NOT be analysed until you Include them."
            in page
        )
        assert b"You can run discovery now and finish reviewing the package later." in page

    asyncio.run(scenario())


def test_discovery_before_run_shows_included_and_undecided_counts(
    coverage_app: ModuleType,
) -> None:
    async def scenario() -> None:
        await _partial_package(coverage_app, "Before-Run")
        status, _, page = await request(
            coverage_app.app, "GET", f"/bids/{BID_ID}/deliverable-discovery"
        )
        assert status == 200
        assert b"1 Included file(s) will be analysed;" in page
        assert b"2 file(s) are still undecided and will not be." in page
        assert b"Finish reviewing the package" in page

    asyncio.run(scenario())


def test_run_reports_processed_file_and_candidate_without_honesty_warning(
    coverage_app: ModuleType,
) -> None:
    async def scenario() -> None:
        await _partial_package(coverage_app, "Candidate-Run")
        _, page = await post_run_and_finish(
            coverage_app,
            BID_ID,
            "coverage-candidate",
        )
        assert (
            b"Text extracted from 1 of 1 file(s). Analysed by the model: 1 fully, 0 partly, "
            b"0 not yet. Could not be read: 0." in page
        )
        assert b"<strong>Analysis complete</strong>" in page
        assert b"found no possible returnables" not in page
        status, _, latest_page = await request(
            coverage_app.app, "GET", f"/bids/{BID_ID}/deliverable-discovery"
        )
        assert status == 200
        assert b"1 of 1 Included file(s) fully analysed" in latest_page
        assert b"Signed Bid Form" in latest_page

    asyncio.run(scenario())


def test_unsupported_included_file_is_named_and_triggers_honesty_warning(
    coverage_app: ModuleType,
) -> None:
    async def scenario() -> None:
        _make_folder(coverage_app, "Unsupported-Run", {"notes.txt": b"synthetic notes"})
        path = await _register_release(
            coverage_app, folder="Unsupported-Run", reference="Unsupported-Run"
        )
        release_id = path.rsplit("/", 1)[-1]
        row = _files_by_name(coverage_app, BID_ID, release_id)["notes.txt"]
        await _review(
            coverage_app,
            path,
            [str(row["file_id"])],
            "ELIGIBLE",
            "include-unsupported",
        )
        _, page = await post_run_and_finish(
            coverage_app,
            BID_ID,
            "coverage-unsupported",
        )
        assert (
            b"Text extracted from 0 of 1 file(s). Analysed by the model: 0 fully, 0 partly, "
            b"0 not yet. Could not be read: 1." in page
        )
        assert b"<strong>Analysis did not produce results</strong>" in page
        assert b"notes.txt" in page
        assert b"file type not supported for discovery (PDF, Word or Excel only)" in page
        assert b'role="alert">This analysis found no possible returnables' in page

    asyncio.run(scenario())


def test_unreachable_model_reports_failure_without_invalid_output_count(
    coverage_app: ModuleType,
) -> None:
    class RaisingLLM:
        def complete_json(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise RuntimeError("synthetic model outage")

    async def scenario() -> None:
        _make_folder(coverage_app, "Failed-Model", {"included.pdf": _included_pdf()})
        path = await _register_release(
            coverage_app, folder="Failed-Model", reference="Failed-Model"
        )
        release_id = path.rsplit("/", 1)[-1]
        row = _files_by_name(coverage_app, BID_ID, release_id)["included.pdf"]
        await _review(
            coverage_app,
            path,
            [str(row["file_id"])],
            "ELIGIBLE",
            "include-failed-model",
        )
        coverage_app.deliverable_discovery_service.llm_client = RaisingLLM()
        coverage_app.discovery_job_runner.llm_client = RaisingLLM()
        _, page = await post_run_and_finish(
            coverage_app,
            BID_ID,
            "coverage-model-failure",
        )
        assert b"MODEL_UNAVAILABLE" in page
        assert b"invalid model output or candidate item(s) discarded" not in page
        assert b'role="alert">This analysis found no possible returnables' in page
        assert b"model output discarded" not in page

    asyncio.run(scenario())


def test_legacy_run_renders_without_claiming_model_analysis(coverage_app: ModuleType) -> None:
    async def scenario() -> None:
        await _partial_package(coverage_app, "Legacy-Run")
        now = datetime.now(UTC)
        coverage_app.deliverable_discovery_repository.create_run(
            DiscoveryRun(
                bid_id=BID_ID,
                operation_id="legacy-run",
                status=RunStatus.SUCCEEDED,
                proposal_count=0,
                actor="Jason",
                started_at=now,
                completed_at=now,
            ),
            [],
            [],
            "Jason",
        )
        status, _, page = await request(
            coverage_app.app, "GET", f"/bids/{BID_ID}/deliverable-discovery"
        )
        assert status == 200
        assert b"recorded before per-file outcomes were kept" in page
        assert b"whether the model analysed them was not recorded for this run" in page
        assert b"0 analysed by the model" not in page
        assert b"<strong>LEGACY</strong>" in page

    asyncio.run(scenario())


def test_register_redirect_lands_on_files_with_server_notice(coverage_app: ModuleType) -> None:
    async def scenario() -> None:
        _make_folder(coverage_app, "Receipt-Landing", {"one.txt": b"1", "two.txt": b"2"})
        location = await _register_raw(coverage_app, "Receipt-Landing")
        parsed = urlsplit(location)
        assert parse_qs(parsed.query) == {"notice": ["received"], "count": ["2"]}
        assert parsed.fragment == "files"
        status, _, page = await request(coverage_app.app, "GET", location)
        assert status == 200
        assert (
            b"Received 2 file(s) into managed storage. Now choose which files ContractIQ may "
            b"analyse." in page
        )

    asyncio.run(scenario())
