"""ASGI scenarios for the separate analysis-inclusion and governing-source decisions."""

from __future__ import annotations

import asyncio
import hashlib
import html
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from scripts.asgi_acceptance_ops08 import request
from tests.unit.deliverable_discovery_job_route_helpers import post_run_and_finish
from tests.unit.test_bid_package_bulk_document_control_ui import BID_ID, _make_bid
from tests.unit.test_deliverable_discovery_service import _pdf_bytes, _StubLLM

OTHER_BID_ID = "B-2026-5555"


async def _post_form(
    app: ModuleType, path: str, pairs: list[tuple[str, str]]
) -> tuple[int, dict[str, str], bytes]:
    return await request(app.app, "POST", path, body=urlencode(pairs).encode())


def _tokens(page: bytes, name: str) -> dict[str, str]:
    pattern = rb'name="' + name.encode() + rb'" value="([^|]+)\|([^"]*)"'
    return {
        match.group(1).decode(): match.group(2).decode() for match in re.finditer(pattern, page)
    }


@pytest.fixture
def governing_ui_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    source = tmp_path / "intake-inbox"
    initial = source / "Mixed-16"
    initial.mkdir(parents=True)
    (initial / "analysis.pdf").write_bytes(
        _pdf_bytes(["Submit the signed Bid Form with the offer to Procurement."])
    )
    for index in range(2, 17):
        (initial / f"f{index:02d}.txt").write_bytes(f"synthetic file {index}".encode())
    addendum = source / "Addendum-01"
    addendum.mkdir()
    for index in range(1, 4):
        (addendum / f"a{index:02d}.txt").write_bytes(f"synthetic addendum {index}".encode())
    other = source / "Other-Bid"
    other.mkdir()
    (other / "other.txt").write_bytes(b"other Bid source")

    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setenv("CONTRACTIQ_DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setenv("CONTRACTIQ_PROPOSAL_ARTIFACT_ROOT", str(tmp_path / "proposal"))
    monkeypatch.setenv("CONTRACTIQ_INTAKE_STORAGE_ROOT", str(tmp_path / "intake"))
    monkeypatch.delenv("CONTRACTIQ_INTAKE_SOURCE_ROOTS", raising=False)
    monkeypatch.setenv("CONTRACTIQ_INTAKE_ROOTS", str(source))
    monkeypatch.setenv("CONTRACTIQ_DISCOVERY_RUNNER", "off")
    previous = sys.modules.pop("app", None)
    import app

    app.deliverable_discovery_service.llm_client = _StubLLM()
    app.discovery_job_runner.llm_client = _StubLLM()
    now = datetime(2026, 9, 12, 12, tzinfo=UTC)
    app.bid_repository.create_bid(_make_bid(BID_ID, "Mixed governing package", now))
    yield app
    sys.modules.pop("app", None)
    if previous is not None:
        sys.modules["app"] = previous


async def _register_release(
    app: ModuleType,
    *,
    bid_id: str = BID_ID,
    folder: str = "Mixed-16",
    release_type: str = "INITIAL_PACKAGE",
    reference: str = "Synthetic initial package",
    mode: str = "initial",
) -> str:
    root = f"/bids/{bid_id}/package-intake-addenda"
    status, _, preview = await request(
        app.app,
        "POST",
        f"{root}/preview",
        {
            "release_type": release_type,
            "source_root_alias": "inbox_1",
            "source_folder": folder,
            "intake_mode": mode,
            "exact_customer_reference": reference,
            "customer_issue_date": "2026-09-12",
            "received_channel": "EMAIL",
        },
    )
    assert status == 200, preview[:500]
    token = re.search(rb'name="confirmation_token" value="([^"]+)"', preview)
    assert token is not None
    status, headers, body = await request(
        app.app,
        "POST",
        f"{root}/register",
        {"confirmation_token": token.group(1).decode()},
    )
    assert status == 303, body[:500]
    location = urlsplit(headers["location"])
    assert parse_qs(location.query).get("notice") == ["received"]
    assert parse_qs(location.query).get("count")
    assert location.fragment == "files"
    return location.path


def _files_by_name(app: ModuleType, bid_id: str, release_id: str) -> dict[str, dict[str, object]]:
    detail = app.bid_package_service.release_detail(bid_id, release_id)
    return {str(row["original_filename"]): row for row in detail["files"]}


async def _review(
    app: ModuleType,
    release_path: str,
    file_ids: list[str],
    eligibility: str,
    operation_id: str,
) -> bytes:
    status, _, page = await request(app.app, "GET", release_path)
    assert status == 200
    tokens = _tokens(page, "review_tokens")
    pairs = [("operation_id", operation_id), ("analysis_eligibility", eligibility)]
    if eligibility == "EXCLUDED":
        pairs.append(("exclusion_reason", "Not part of the current analysis."))
    for file_id in file_ids:
        pairs.extend([("file_ids", file_id), ("review_tokens", f"{file_id}|{tokens[file_id]}")])
    status, headers, body = await _post_form(app, f"{release_path}/bulk-dispositions", pairs)
    assert status == 303, (headers, body[:500])
    return page


async def _select_governing(
    app: ModuleType,
    release_path: str,
    file_ids: list[str],
    operation_id: str,
    disposition: str = "GOVERNING",
) -> None:
    status, _, page = await request(app.app, "GET", release_path)
    assert status == 200
    tokens = _tokens(page, "governing_tokens")
    pairs = [
        ("operation_id", operation_id),
        ("governing_disposition", disposition),
    ]
    for file_id in file_ids:
        pairs.extend([("file_ids", file_id), ("governing_tokens", f"{file_id}|{tokens[file_id]}")])
    status, headers, body = await _post_form(app, f"{release_path}/bulk-governing-sources", pairs)
    assert status == 303, (headers, body[:500])


async def _control_governing(
    app: ModuleType,
    release_path: str,
    file_ids: list[str],
    *,
    category: str = "SOLICITATION",
) -> None:
    status, _, page = await request(app.app, "GET", release_path)
    assert status == 200
    tokens = _tokens(page, "control_tokens")
    pairs = [("category", category), ("version_label", "Original")]
    for file_id in file_ids:
        pairs.extend([("file_ids", file_id), ("control_tokens", f"{file_id}|{tokens[file_id]}")])
    status, headers, body = await _post_form(
        app, f"{release_path}/bulk-controlled-documents", pairs
    )
    assert status == 303, (headers, body[:500])


async def _publish(
    app: ModuleType,
    release_ids: list[str],
    operation_id: str,
    *,
    expected_snapshot_id: str = "",
) -> tuple[int, bytes]:
    pairs = [
        ("operation_id", operation_id),
        ("expected_current_snapshot_id", expected_snapshot_id),
        ("label", operation_id),
    ]
    pairs.extend(("release_ids", release_id) for release_id in release_ids)
    status, _, body = await _post_form(
        app, f"/bids/{BID_ID}/package-intake-addenda/basis-snapshots", pairs
    )
    return status, body


def _snapshot_documents(app: ModuleType, snapshot_id: str) -> list[dict[str, object]]:
    with app.db._conn() as conn:
        rows = conn.execute(
            """SELECT * FROM bid_basis_snapshot_documents
            WHERE snapshot_id=? ORDER BY source_received_file_id""",
            (snapshot_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _governing_event_count(app: ModuleType) -> int:
    with app.db._conn() as conn:
        return int(
            conn.execute("SELECT count(*) FROM bid_received_file_governing_events").fetchone()[0]
        )


def test_mixed_16_file_package_separates_analysis_discovery_and_governing_basis(
    governing_ui_app: ModuleType,
) -> None:
    async def scenario() -> None:
        app = governing_ui_app
        release_path = await _register_release(app)
        release_id = release_path.rsplit("/", 1)[-1]
        files = _files_by_name(app, BID_ID, release_id)
        analysis_id = str(files["analysis.pdf"]["file_id"])
        other_ids = [str(files[f"f{index:02d}.txt"]["file_id"]) for index in range(2, 17)]
        initially_included = [analysis_id, *other_ids[:12]]
        initially_excluded = other_ids[12:14]
        initially_undecided = other_ids[14]

        await _review(app, release_path, initially_included, "ELIGIBLE", "mixed-include-13")
        await _review(app, release_path, initially_excluded, "EXCLUDED", "mixed-exclude-2")
        status, _, page = await request(app.app, "GET", release_path)
        assert status == 200
        assert b"1 of 16 received file(s) still need a review decision" in page
        assert b"Find required Bid submissions</a> using the included files" not in page
        assert b"No file has been selected as a governing Bid Basis source yet" in page
        assert b"This package is ready" not in page

        governing_ids = other_ids[:5]
        await _select_governing(app, release_path, governing_ids, "mixed-governing-5")
        await _review(app, release_path, initially_excluded, "ELIGIBLE", "mixed-reinclude-2")
        await _review(app, release_path, [initially_undecided], "ELIGIBLE", "mixed-finish-review")

        status, _, page = await request(app.app, "GET", release_path)
        assert status == 200
        assert b"All received files have been reviewed" in page
        assert b"16 file(s) are Included for analysis" in page
        assert b">Run deliverables discovery</a>" in page
        assert (
            b"5 governing source file(s) selected; 5 still need exact controlled versions" in page
        )
        assert b"This package is ready" not in page
        assert set(_tokens(page, "control_tokens")) == set(governing_ids)
        analysis_only_ids = {analysis_id, *other_ids} - set(governing_ids)
        assert len(analysis_only_ids) == 11
        section = page.split(b'id="document-control"', 1)[1].split(b"</section>", 1)[0]
        for name, row in files.items():
            file_id = str(row["file_id"])
            if file_id in governing_ids:
                assert name.encode() in section
            else:
                assert name.encode() not in section

        await _control_governing(app, release_path, governing_ids)
        status, _, page = await request(app.app, "GET", release_path)
        assert status == 200
        assert b"This package is ready" in page
        assert b"confirm the current Bid basis" in page

        entries = app.document_service.list_register_entries(bid_id=BID_ID)
        assert len(entries) == 5
        assert len({entry.document.document_id for entry in entries}) == 5
        expected_by_title = {
            Path(name).stem: hashlib.sha256(
                f"synthetic file {int(Path(name).stem[1:])}".encode()
            ).hexdigest()
            for name, row in files.items()
            if str(row["file_id"]) in governing_ids
        }
        assert {
            entry.document.title: entry.current_version.sha256_digest for entry in entries
        } == expected_by_title

        status, body = await _publish(app, [release_id], "mixed-basis-1")
        assert status == 303, body[:500]
        snapshot = app.bid_package_repository.current_snapshot(BID_ID)
        assert snapshot is not None
        rows = _snapshot_documents(app, str(snapshot["snapshot_id"]))
        assert {str(row["source_received_file_id"]) for row in rows} == set(governing_ids)
        assert len(rows) == 5
        assert analysis_id not in {str(row["source_received_file_id"]) for row in rows}

    asyncio.run(scenario())


def test_analysis_only_discovery_runs_before_basis_and_survives_publication(
    governing_ui_app: ModuleType,
) -> None:
    async def scenario() -> None:
        app = governing_ui_app
        release_path = await _register_release(app)
        release_id = release_path.rsplit("/", 1)[-1]
        files = _files_by_name(app, BID_ID, release_id)
        file_ids = [str(row["file_id"]) for row in files.values()]
        analysis_id = str(files["analysis.pdf"]["file_id"])
        governing_id = str(files["f02.txt"]["file_id"])
        await _review(app, release_path, file_ids, "ELIGIBLE", "discovery-review-all")

        with app.db._conn() as conn:
            assert conn.execute("SELECT count(*) FROM bid_basis_snapshots").fetchone()[0] == 0
            assert (
                conn.execute("SELECT count(*) FROM bid_received_file_document_links").fetchone()[0]
                == 0
            )
        await post_run_and_finish(
            app,
            BID_ID,
            "pre-basis-discovery",
        )
        proposals_before = app.deliverable_discovery_service.list_review_queue(BID_ID)
        assert proposals_before
        assert any(
            source["received_file_id"] == analysis_id
            for proposal in proposals_before
            for source in proposal["sources"]
        )
        with app.db._conn() as conn:
            run_before = dict(
                conn.execute(
                    "SELECT * FROM deliverable_discovery_runs WHERE operation_id=?",
                    ("pre-basis-discovery",),
                ).fetchone()
            )

        await _select_governing(app, release_path, [governing_id], "discovery-governing")
        await _control_governing(app, release_path, [governing_id])
        status, body = await _publish(app, [release_id], "discovery-later-basis")
        assert status == 303, body[:500]
        proposals_after = app.deliverable_discovery_service.list_review_queue(BID_ID)
        with app.db._conn() as conn:
            run_after = dict(
                conn.execute(
                    "SELECT * FROM deliverable_discovery_runs WHERE operation_id=?",
                    ("pre-basis-discovery",),
                ).fetchone()
            )
        assert run_after == run_before
        assert proposals_after == proposals_before
        snapshot = app.bid_package_repository.current_snapshot(BID_ID)
        assert snapshot is not None
        assert {
            str(row["source_received_file_id"])
            for row in _snapshot_documents(app, str(snapshot["snapshot_id"]))
        } == {governing_id}

    asyncio.run(scenario())


def test_addendum_snapshot_carries_forward_prior_governing_rows(
    governing_ui_app: ModuleType,
) -> None:
    async def scenario() -> None:
        app = governing_ui_app
        initial_path = await _register_release(app)
        initial_id = initial_path.rsplit("/", 1)[-1]
        initial_files = _files_by_name(app, BID_ID, initial_id)
        first_file = str(initial_files["f02.txt"]["file_id"])
        remaining = [
            str(row["file_id"])
            for row in initial_files.values()
            if str(row["file_id"]) != first_file
        ]
        await _review(app, initial_path, [first_file], "ELIGIBLE", "addendum-first-include")
        await _review(app, initial_path, remaining, "EXCLUDED", "addendum-first-exclude")
        await _select_governing(app, initial_path, [first_file], "addendum-first-governing")
        await _control_governing(app, initial_path, [first_file])
        status, body = await _publish(app, [initial_id], "addendum-basis-1")
        assert status == 303, body[:500]
        first_snapshot = app.bid_package_repository.current_snapshot(BID_ID)
        assert first_snapshot is not None
        first_snapshot_id = str(first_snapshot["snapshot_id"])
        first_rows_before = _snapshot_documents(app, first_snapshot_id)

        addendum_path = await _register_release(
            app,
            folder="Addendum-01",
            release_type="ADDENDUM",
            reference="Addendum 1",
            mode="addendum",
        )
        addendum_id = addendum_path.rsplit("/", 1)[-1]
        addendum_files = _files_by_name(app, BID_ID, addendum_id)
        addendum_file_ids = [str(row["file_id"]) for row in addendum_files.values()]
        await _review(app, addendum_path, addendum_file_ids, "ELIGIBLE", "addendum-review")
        await _select_governing(app, addendum_path, addendum_file_ids[:1], "addendum-governing")
        await _control_governing(app, addendum_path, addendum_file_ids[:1], category="ADDENDUM")
        status, body = await _publish(
            app,
            [initial_id, addendum_id],
            "addendum-basis-2",
            expected_snapshot_id=first_snapshot_id,
        )
        assert status == 303, body[:500]
        second_snapshot = app.bid_package_repository.current_snapshot(BID_ID)
        assert second_snapshot is not None
        second_snapshot_id = str(second_snapshot["snapshot_id"])
        assert app.bid_package_repository.snapshot_release_ids(second_snapshot_id) == (
            initial_id,
            addendum_id,
        )
        assert _snapshot_documents(app, first_snapshot_id) == first_rows_before
        second_rows = _snapshot_documents(app, second_snapshot_id)
        assert {str(row["source_received_file_id"]) for row in second_rows} == {
            first_file,
            addendum_file_ids[0],
        }

    asyncio.run(scenario())


def test_invalid_governing_selection_is_atomic_and_unlinked_source_blocks_publication(
    governing_ui_app: ModuleType,
) -> None:
    async def scenario() -> None:
        app = governing_ui_app
        release_path = await _register_release(app)
        release_id = release_path.rsplit("/", 1)[-1]
        files = _files_by_name(app, BID_ID, release_id)
        eligible_ids = [str(files["f02.txt"]["file_id"]), str(files["f03.txt"]["file_id"])]
        excluded_id = str(files["f04.txt"]["file_id"])
        not_assessed_id = str(files["f05.txt"]["file_id"])
        rest = [
            str(row["file_id"])
            for name, row in files.items()
            if str(row["file_id"]) not in {*eligible_ids, excluded_id, not_assessed_id}
        ]
        await _review(app, release_path, eligible_ids, "ELIGIBLE", "invalid-eligible")
        await _review(app, release_path, [excluded_id], "EXCLUDED", "invalid-excluded")
        await _review(app, release_path, rest, "EXCLUDED", "invalid-rest")

        async def invalid(pairs: list[tuple[str, str]], expected: str) -> None:
            status, _, body = await _post_form(app, f"{release_path}/bulk-governing-sources", pairs)
            assert status == 422
            rendered = html.unescape(body.decode())
            assert expected in rendered
            assert "Refresh this package and try again" in rendered
            assert _governing_event_count(app) == 0

        await invalid(
            [
                ("operation_id", "invalid-excluded-selection"),
                ("governing_disposition", "GOVERNING"),
                ("file_ids", eligible_ids[0]),
                ("governing_tokens", f"{eligible_ids[0]}|"),
                ("file_ids", excluded_id),
                ("governing_tokens", f"{excluded_id}|"),
            ],
            'must be marked "Include for analysis" first',
        )
        await invalid(
            [
                ("operation_id", "invalid-not-assessed"),
                ("governing_disposition", "GOVERNING"),
                ("file_ids", not_assessed_id),
                ("governing_tokens", f"{not_assessed_id}|"),
            ],
            'must be marked "Include for analysis" first',
        )
        await invalid(
            [
                ("operation_id", "invalid-stale"),
                ("governing_disposition", "GOVERNING"),
                ("file_ids", eligible_ids[0]),
                ("governing_tokens", f"{eligible_ids[0]}|GSE-stale"),
            ],
            "governing selections changed; refresh the package before selecting",
        )
        await invalid(
            [
                ("operation_id", "invalid-duplicate"),
                ("governing_disposition", "GOVERNING"),
                ("file_ids", eligible_ids[0]),
                ("file_ids", eligible_ids[0]),
                ("governing_tokens", f"{eligible_ids[0]}|"),
            ],
            "a received file can only be selected once",
        )
        status, headers, body = await _post_form(
            app,
            f"{release_path}/bulk-governing-sources",
            [
                ("operation_id", "invalid-empty"),
                ("governing_disposition", "GOVERNING"),
            ],
        )
        assert status == 303
        assert headers["location"] == (f"{release_path}?notice=none_selected&count=0#files")
        assert b"Tuple" not in body
        assert _governing_event_count(app) == 0

        await _review(
            app,
            release_path,
            [not_assessed_id],
            "EXCLUDED",
            "invalid-finish-review",
        )
        await _select_governing(app, release_path, eligible_ids, "valid-two-governing")
        status, body = await _publish(app, [release_id], "unlinked-basis")
        assert status == 422
        assert b"governing file" in body
        assert b"lacks an exact controlled-version link" in body
        await _control_governing(app, release_path, eligible_ids)
        status, body = await _publish(app, [release_id], "valid-basis-1")
        assert status == 303, body[:500]
        first_snapshot = app.bid_package_repository.current_snapshot(BID_ID)
        assert first_snapshot is not None
        first_snapshot_id = str(first_snapshot["snapshot_id"])
        first_rows = _snapshot_documents(app, first_snapshot_id)
        assert {str(row["source_received_file_id"]) for row in first_rows} == set(eligible_ids)
        audits = [
            row
            for row in app.bid_repository.list_audit(BID_ID)
            if row.action == "bid_received_file_governing_recorded"
        ]
        assert len(audits) == _governing_event_count(app) == 2

    asyncio.run(scenario())


def test_unselecting_governing_file_removes_it_only_from_next_snapshot(
    governing_ui_app: ModuleType,
) -> None:
    async def scenario() -> None:
        app = governing_ui_app
        release_path = await _register_release(app)
        release_id = release_path.rsplit("/", 1)[-1]
        files = _files_by_name(app, BID_ID, release_id)
        governing_ids = [str(files["f02.txt"]["file_id"]), str(files["f03.txt"]["file_id"])]
        non_governing_ids = [
            str(row["file_id"])
            for row in files.values()
            if str(row["file_id"]) not in governing_ids
        ]
        await _review(app, release_path, governing_ids, "ELIGIBLE", "unselect-eligible")
        await _review(app, release_path, non_governing_ids, "EXCLUDED", "unselect-excluded")
        await _select_governing(app, release_path, governing_ids, "unselect-select-two")
        await _control_governing(app, release_path, governing_ids)
        status, body = await _publish(app, [release_id], "unselect-basis-1")
        assert status == 303, body[:500]
        first_snapshot = app.bid_package_repository.current_snapshot(BID_ID)
        assert first_snapshot is not None
        first_snapshot_id = str(first_snapshot["snapshot_id"])
        first_rows = _snapshot_documents(app, first_snapshot_id)
        assert {str(row["source_received_file_id"]) for row in first_rows} == set(governing_ids)

        await _select_governing(
            app,
            release_path,
            [governing_ids[1]],
            "unselect-one-governing",
            disposition="NOT_GOVERNING",
        )
        audits = [
            row
            for row in app.bid_repository.list_audit(BID_ID)
            if row.action == "bid_received_file_governing_recorded"
        ]
        assert len(audits) == _governing_event_count(app) == 3
        status, body = await _publish(
            app,
            [release_id],
            "valid-basis-2",
            expected_snapshot_id=first_snapshot_id,
        )
        assert status == 303, body[:500]
        second_snapshot = app.bid_package_repository.current_snapshot(BID_ID)
        assert second_snapshot is not None
        second_rows = _snapshot_documents(app, str(second_snapshot["snapshot_id"]))
        old_rows_after = _snapshot_documents(app, first_snapshot_id)
        assert old_rows_after == first_rows
        assert governing_ids[1] in {str(row["source_received_file_id"]) for row in old_rows_after}
        old_rows_by_source = {str(row["source_received_file_id"]): row for row in old_rows_after}
        second_rows_by_source = {str(row["source_received_file_id"]): row for row in second_rows}
        assert governing_ids[0] in second_rows_by_source
        assert (
            second_rows_by_source[governing_ids[0]]["document_version_id"]
            == old_rows_by_source[governing_ids[0]]["document_version_id"]
        )
        assert {str(row["source_received_file_id"]) for row in second_rows} == {governing_ids[0]}

    asyncio.run(scenario())


def test_cross_bid_governing_and_controlled_links_are_rejected_without_mutation(
    governing_ui_app: ModuleType,
) -> None:
    async def scenario() -> None:
        app = governing_ui_app
        now = datetime(2026, 9, 12, 13, tzinfo=UTC)
        app.bid_repository.create_bid(_make_bid(OTHER_BID_ID, "Other Bid", now))
        this_path = await _register_release(app)
        other_path = await _register_release(
            app,
            bid_id=OTHER_BID_ID,
            folder="Other-Bid",
            reference="Other Bid package",
        )
        this_release_id = this_path.rsplit("/", 1)[-1]
        other_release_id = other_path.rsplit("/", 1)[-1]
        this_file = next(iter(_files_by_name(app, BID_ID, this_release_id).values()))
        other_file = next(iter(_files_by_name(app, OTHER_BID_ID, other_release_id).values()))
        this_file_id = str(this_file["file_id"])
        other_file_id = str(other_file["file_id"])
        await _review(app, this_path, [this_file_id], "ELIGIBLE", "cross-this-review")
        await _review(app, other_path, [other_file_id], "ELIGIBLE", "cross-other-review")

        attempts = [
            (
                f"{this_path}/bulk-governing-sources",
                this_release_id,
                other_file_id,
                "cross-file-under-this-bid",
            ),
            (
                f"/bids/{OTHER_BID_ID}/package-intake-addenda/releases/{this_release_id}/bulk-governing-sources",
                this_release_id,
                this_file_id,
                "cross-release-under-other-bid",
            ),
        ]
        for path, _release_id, file_id, operation_id in attempts:
            status, _, body = await _post_form(
                app,
                path,
                [
                    ("operation_id", operation_id),
                    ("governing_disposition", "GOVERNING"),
                    ("file_ids", file_id),
                    ("governing_tokens", f"{file_id}|"),
                ],
            )
            assert status == 422, body[:500]
            assert b"this Bid" in body
        assert _governing_event_count(app) == 0

        await _select_governing(app, other_path, [other_file_id], "other-valid-governing")
        await _control_governing(app, other_path, [other_file_id])
        other_entry = app.document_service.list_register_entries(bid_id=OTHER_BID_ID)[0]
        status, _, body = await request(
            app.app,
            "POST",
            f"/bids/{BID_ID}/package-intake-addenda/files/{this_file_id}/document-links",
            {
                "release_id": this_release_id,
                "document_version_id": other_entry.current_version.document_version_id,
                "relationship": "EXACT_BYTES",
                "operation_id": "cross-bid-version-link",
            },
        )
        assert status == 422, body[:500]
        assert b"controlled version must belong to this Bid" in body
        with app.db._conn() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM bid_received_file_document_links WHERE file_id=?",
                    (this_file_id,),
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    """SELECT count(*) FROM bid_received_file_governing_events e
                JOIN bid_received_files f ON f.file_id=e.file_id
                WHERE f.file_id=?""",
                    (this_file_id,),
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM bid_received_file_document_links WHERE file_id=?",
                    (other_file_id,),
                ).fetchone()[0]
                == 1
            )

    asyncio.run(scenario())
