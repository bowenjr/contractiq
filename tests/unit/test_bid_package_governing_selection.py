from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.bid_package_intake import (
    AnalysisEligibility,
    GoverningDisposition,
    GoverningSelectionCreate,
    GoverningSelectionItem,
)
from core.bid_package_repository import (
    OPS_11_MIGRATION_ID,
    BidPackageRepository,
    IntakeConflictError,
    IntakeNotFoundError,
    StaleIntakeError,
)
from core.database import Database
from tests.unit.test_bid_package_intake import _bulk_command, _bulk_release
from tests.unit.test_bid_package_storage import BID_ID, _command


def _governing_command(
    release_id: str,
    files: list[dict[str, object]],
    *,
    disposition: GoverningDisposition = GoverningDisposition.GOVERNING,
    operation_id: str = "governing-selection-1",
) -> GoverningSelectionCreate:
    return GoverningSelectionCreate(
        release_id=release_id,
        items=tuple(
            GoverningSelectionItem(
                file_id=str(row["file_id"]),
                supersedes_event_id=(
                    str(row["governing_event_id"])
                    if row.get("governing_event_id") is not None
                    else None
                ),
            )
            for row in files
        ),
        governing_disposition=disposition,
        operation_id=operation_id,
    )


def _governing_counts(database: Database) -> tuple[int, int]:
    with database._conn() as conn:  # type: ignore[attr-defined]
        events = conn.execute("SELECT count(*) FROM bid_received_file_governing_events").fetchone()[
            0
        ]
        audits = conn.execute(
            "SELECT count(*) FROM audit_log WHERE action='bid_received_file_governing_recorded'"
        ).fetchone()[0]
    return int(events), int(audits)


def _files_with_governing_state(
    repository: BidPackageRepository, release_id: str
) -> list[dict[str, object]]:
    files = repository.release_detail(BID_ID, release_id)["files"]
    return [
        {**row, **repository.current_governing_disposition(str(row["file_id"]))} for row in files
    ]


def test_governing_migration_is_idempotent_and_immutable(tmp_path: Path) -> None:
    _service, repository, database, release_id = _bulk_release(tmp_path)
    file_id = str(repository.release_detail(BID_ID, release_id)["files"][0]["file_id"])

    repository.__class__(database)

    with database._conn() as conn:  # type: ignore[attr-defined]
        migrations = conn.execute(
            "SELECT migration_id FROM bid_package_intake_schema_migrations"
        ).fetchall()
        table = conn.execute(
            """SELECT name FROM sqlite_master
            WHERE type='table' AND name='bid_received_file_governing_events'"""
        ).fetchone()
        assert [row["migration_id"] for row in migrations] == [OPS_11_MIGRATION_ID]
        assert table is not None
        conn.execute(
            """INSERT INTO bid_received_file_governing_events VALUES(?,?,?,?,?,?,?,?)""",
            (
                "GSE-immutable",
                file_id,
                "NOT_GOVERNING",
                None,
                "immutable-test",
                "Jason",
                "2026-09-11T12:00:00+00:00",
                "{}",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                """UPDATE bid_received_file_governing_events
                SET actor='changed' WHERE governing_event_id='GSE-immutable'"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute(
                """DELETE FROM bid_received_file_governing_events
                WHERE governing_event_id='GSE-immutable'"""
            )


def test_fresh_files_have_unset_governing_disposition(tmp_path: Path) -> None:
    _service, repository, _database, release_id = _bulk_release(tmp_path)

    for file_row in repository.release_detail(BID_ID, release_id)["files"]:
        assert repository.current_governing_disposition(str(file_row["file_id"])) == {
            "file_id": file_row["file_id"],
            "governing_event_id": None,
            "governing_disposition": "UNSET",
        }


def test_eligible_files_can_be_selected_without_coupling_to_later_review(
    tmp_path: Path,
) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    files = repository.release_detail(BID_ID, release_id)["files"]
    selected = files[:2]
    service.bulk_classify_files(
        BID_ID,
        _bulk_command(release_id, selected, operation_id="eligible-for-governing"),
        "Jason",
    )

    result = service.select_governing_sources(
        BID_ID,
        _governing_command(release_id, selected, operation_id="select-governing"),
        "Jason",
    )

    assert len(result) == 2
    assert {row["governing_disposition"] for row in result} == {"GOVERNING"}
    assert _governing_counts(database) == (2, 2)
    untouched = repository.current_governing_disposition(str(files[2]["file_id"]))
    assert untouched["governing_disposition"] == "UNSET"
    first_before = repository.current_governing_disposition(str(selected[0]["file_id"]))
    reviewed_files = repository.release_detail(BID_ID, release_id)["files"]
    service.bulk_classify_files(
        BID_ID,
        _bulk_command(
            release_id,
            reviewed_files[:1],
            eligibility=AnalysisEligibility.EXCLUDED,
            operation_id="review-after-governing",
        ),
        "Jason",
    )
    assert repository.current_governing_disposition(str(selected[0]["file_id"])) == first_before
    governing_after_review = _files_with_governing_state(repository, release_id)[:1]
    assert (
        service.select_governing_sources(
            BID_ID,
            _governing_command(
                release_id,
                governing_after_review,
                operation_id="repeat-governing-after-review",
            ),
            "Jason",
        )
        == ()
    )
    assert _governing_counts(database) == (2, 2)


@pytest.mark.parametrize(
    "ineligible_state", [AnalysisEligibility.NOT_ASSESSED, AnalysisEligibility.EXCLUDED]
)
def test_ineligible_governing_selection_is_all_or_nothing(
    tmp_path: Path, ineligible_state: AnalysisEligibility
) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    files = repository.release_detail(BID_ID, release_id)["files"]
    service.bulk_classify_files(
        BID_ID,
        _bulk_command(release_id, files[:1], operation_id="first-is-eligible"),
        "Jason",
    )
    if ineligible_state is AnalysisEligibility.EXCLUDED:
        current_files = repository.release_detail(BID_ID, release_id)["files"]
        service.bulk_classify_files(
            BID_ID,
            _bulk_command(
                release_id,
                current_files[1:2],
                eligibility=AnalysisEligibility.EXCLUDED,
                operation_id="second-is-excluded",
            ),
            "Jason",
        )

    with pytest.raises(IntakeConflictError, match="Include for analysis"):
        service.select_governing_sources(
            BID_ID,
            _governing_command(release_id, files[:2], operation_id="atomic-rejection"),
            "Jason",
        )

    assert _governing_counts(database) == (0, 0)


@pytest.mark.parametrize("supersedes_event_id", [None, "GSE-stale"])
def test_stale_or_missing_governing_token_does_not_mutate(
    tmp_path: Path, supersedes_event_id: str | None
) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    files = repository.release_detail(BID_ID, release_id)["files"]
    service.bulk_classify_files(BID_ID, _bulk_command(release_id, files[:1]), "Jason")
    service.select_governing_sources(
        BID_ID,
        _governing_command(release_id, files[:1], operation_id="initial-governing"),
        "Jason",
    )
    stale_command = GoverningSelectionCreate(
        release_id=release_id,
        items=(
            GoverningSelectionItem(
                file_id=str(files[0]["file_id"]),
                supersedes_event_id=supersedes_event_id,
            ),
        ),
        governing_disposition=GoverningDisposition.NOT_GOVERNING,
        operation_id="stale-correction",
    )

    with pytest.raises(StaleIntakeError, match="refresh"):
        service.select_governing_sources(BID_ID, stale_command, "Jason")

    assert _governing_counts(database) == (1, 1)


def test_cross_release_and_cross_bid_files_do_not_mutate(tmp_path: Path) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    first_files = repository.release_detail(BID_ID, release_id)["files"]
    preview = service.preview(BID_ID, "landing")
    second_release = service.register_release(
        _command(preview.source_fingerprint, "second-release"), "Jason"
    )
    second_file = repository.release_detail(BID_ID, str(second_release["release_id"]))["files"][0]

    with pytest.raises(IntakeConflictError, match="displayed received package"):
        service.select_governing_sources(
            BID_ID,
            _governing_command(release_id, [second_file], operation_id="cross-release"),
            "Jason",
        )
    assert _governing_counts(database) == (0, 0)

    existing_bid = service.bid_repository.get_bid(BID_ID)
    assert existing_bid is not None
    other_bid_id = "B-2026-0012"
    service.bid_repository.create_bid(existing_bid.model_copy(update={"bid_id": other_bid_id}))
    other_preview = service.preview(other_bid_id, "landing")
    other_release = service.register_release(
        _command(other_preview.source_fingerprint, "other-bid-release").model_copy(
            update={"bid_id": other_bid_id}
        ),
        "Jason",
    )
    other_file = repository.release_detail(other_bid_id, str(other_release["release_id"]))["files"][
        0
    ]
    with pytest.raises(IntakeNotFoundError, match="selected received file not found"):
        service.select_governing_sources(
            BID_ID,
            _governing_command(release_id, [other_file], operation_id="cross-bid"),
            "Jason",
        )
    assert _governing_counts(database) == (0, 0)
    assert all(
        repository.current_governing_disposition(str(row["file_id"]))["governing_disposition"]
        == "UNSET"
        for row in first_files
    )


def test_audit_failure_rolls_back_every_governing_event(tmp_path: Path) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    files = repository.release_detail(BID_ID, release_id)["files"]
    service.bulk_classify_files(BID_ID, _bulk_command(release_id, files[:2]), "Jason")
    original_audit = repository._audit

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.IntegrityError("injected audit failure")

    repository._audit = fail_audit  # type: ignore[method-assign,assignment]
    with pytest.raises(sqlite3.IntegrityError, match="injected audit failure"):
        service.select_governing_sources(
            BID_ID,
            _governing_command(release_id, files[:2], operation_id="audit-failure"),
            "Jason",
        )
    repository._audit = original_audit  # type: ignore[method-assign,assignment]

    assert _governing_counts(database) == (0, 0)


def test_repeat_of_current_governing_state_is_a_no_op(tmp_path: Path) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    files = repository.release_detail(BID_ID, release_id)["files"]
    fresh_not_governing = service.select_governing_sources(
        BID_ID,
        _governing_command(
            release_id,
            files[:1],
            disposition=GoverningDisposition.NOT_GOVERNING,
            operation_id="fresh-not-governing",
        ),
        "Jason",
    )
    assert fresh_not_governing == ()
    assert _governing_counts(database) == (0, 0)
    service.bulk_classify_files(BID_ID, _bulk_command(release_id, files[:1]), "Jason")
    service.select_governing_sources(
        BID_ID,
        _governing_command(release_id, files[:1], operation_id="first-selection"),
        "Jason",
    )
    replay = service.select_governing_sources(
        BID_ID,
        _governing_command(release_id, files[:1], operation_id="first-selection"),
        "Jason",
    )
    current = _files_with_governing_state(repository, release_id)[:1]

    result = service.select_governing_sources(
        BID_ID,
        _governing_command(release_id, current, operation_id="same-selection-new-operation"),
        "Jason",
    )

    assert replay == ()
    assert result == ()
    assert _governing_counts(database) == (1, 1)


def test_governing_correction_appends_and_preserves_original(tmp_path: Path) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    files = repository.release_detail(BID_ID, release_id)["files"]
    service.bulk_classify_files(BID_ID, _bulk_command(release_id, files[:1]), "Jason")
    first = service.select_governing_sources(
        BID_ID,
        _governing_command(release_id, files[:1], operation_id="governing-first"),
        "Jason",
    )[0]
    current = _files_with_governing_state(repository, release_id)[:1]

    correction = service.select_governing_sources(
        BID_ID,
        _governing_command(
            release_id,
            current,
            disposition=GoverningDisposition.NOT_GOVERNING,
            operation_id="not-governing-correction",
        ),
        "Jason",
    )[0]

    assert correction["governing_event_id"] != first["governing_event_id"]
    assert correction["governing_disposition"] == "NOT_GOVERNING"
    with database._conn() as conn:  # type: ignore[attr-defined]
        rows = conn.execute(
            """SELECT governing_event_id,governing_disposition,supersedes_event_id
            FROM bid_received_file_governing_events ORDER BY rowid"""
        ).fetchall()
    assert [row["governing_disposition"] for row in rows] == ["GOVERNING", "NOT_GOVERNING"]
    assert rows[0]["supersedes_event_id"] is None
    assert rows[1]["supersedes_event_id"] == rows[0]["governing_event_id"]
    assert _governing_counts(database) == (2, 2)


def test_governing_request_rejects_unset_and_duplicate_files() -> None:
    item = GoverningSelectionItem(file_id="RF-1")
    with pytest.raises(ValidationError, match="UNSET"):
        GoverningSelectionCreate(
            release_id="REL-1",
            items=(item,),
            governing_disposition=GoverningDisposition.UNSET,
            operation_id="unset",
        )
    with pytest.raises(ValidationError, match="only be selected once"):
        GoverningSelectionCreate(
            release_id="REL-1",
            items=(item, item),
            governing_disposition=GoverningDisposition.GOVERNING,
            operation_id="duplicate",
        )
