from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from core.approval_repository import ApprovalRepository
from core.bid_package_intake import ReleaseChannel, ReleaseRegistration, ReleaseType
from core.bid_package_repository import BidPackageRepository
from core.bid_package_service import BidPackageService
from core.bid_package_storage import (
    BidPackageStorage,
    IntakeLimitError,
    IntakePublicationError,
    SourceChangedError,
    UnsafeIntakeSourceError,
    parse_intake_roots,
)
from core.bid_repository import BidRepository
from core.database import Database
from core.document_repository import DocumentRepository
from core.enums import BidLevel, BidStatus, CustomerType, Gate
from core.schemas import Bid
from core.work_item_repository import WorkItemRepository
from core.work_item_service import WorkItemService

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)
BID_ID = "B-2026-0011"


def _service(
    tmp_path: Path,
    *,
    max_files: int = 1_000,
    max_total: int = 1_073_741_824,
    max_file: int = 262_144_000,
    max_path: int = 2_048,
) -> tuple[BidPackageService, BidPackageRepository, Database, Path, Path]:
    source = tmp_path / "source"
    source.mkdir()
    managed = tmp_path / "managed"
    database = Database(tmp_path / "ops11.db")
    bids = BidRepository(database)
    DocumentRepository(database)
    work = WorkItemRepository(database)
    ApprovalRepository(database)
    bids.create_bid(
        Bid(
            bid_id=BID_ID,
            customer="Synthetic EPC",
            customer_type=CustomerType.EPC,
            project_name="Synthetic Package",
            sales_owner="Sales",
            bc_owner="Jason",
            release_date=date(2026, 9, 1),
            customer_due_date=date(2026, 10, 1),
            internal_due_date=date(2026, 9, 25),
            estimated_value=Decimal("1000"),
            classification=BidLevel.LEVEL_0,
            current_gate=Gate.G0,
            status=BidStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    repository = BidPackageRepository(database)
    storage = BidPackageStorage(
        managed,
        {"landing": source},
        max_outer_files=max_files,
        max_total_bytes=max_total,
        max_file_bytes=max_file,
        max_relative_path_length=max_path,
    )
    service = BidPackageService(
        repository,
        bids,
        storage,
        WorkItemService(work, bids),
        now_factory=lambda: NOW,
    )
    return service, repository, database, source, managed


def _command(fingerprint: str, operation_id: str = "register-1") -> ReleaseRegistration:
    return ReleaseRegistration(
        bid_id=BID_ID,
        release_type=ReleaseType.INITIAL_PACKAGE,
        exact_customer_reference="RFP-001",
        customer_issue_date=date(2026, 9, 10),
        received_at=NOW,
        received_channel=ReleaseChannel.EMAIL,
        source_root_alias="landing",
        expected_source_fingerprint=fingerprint,
        operation_id=operation_id,
    )


def test_preview_is_mutation_free_and_confirmation_hashes_managed_copy(tmp_path: Path) -> None:
    service, repository, database, source, managed = _service(tmp_path)
    (source / "folder").mkdir()
    original = b"%PDF synthetic package"
    (source / "folder" / "specification.pdf").write_bytes(original)
    source_before = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }
    managed_before = sorted(path.relative_to(managed) for path in managed.rglob("*"))
    with database._conn() as conn:
        release_count_before = conn.execute(
            "SELECT count(*) FROM bid_received_releases"
        ).fetchone()[0]
        audit_count_before = conn.execute("SELECT count(*) FROM audit_log").fetchone()[0]

    preview = service.preview(BID_ID, "landing")

    assert preview.file_count == 1
    assert source_before == {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }
    assert managed_before == sorted(path.relative_to(managed) for path in managed.rglob("*"))
    with database._conn() as conn:
        assert (
            conn.execute("SELECT count(*) FROM bid_received_releases").fetchone()[0]
            == release_count_before
        )
        assert conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] == audit_count_before

    release = service.register_release(_command(preview.source_fingerprint), "Jason")
    detail = repository.release_detail(BID_ID, str(release["release_id"]))
    file_row = detail["files"][0]
    managed_path = managed / str(file_row["managed_storage_key"])
    assert managed_path.read_bytes() == original
    assert file_row["sha256"] == preview.files[0].sha256
    assert file_row["original_relative_path"] == "folder/specification.pdf"
    run = service.reverify_release(BID_ID, str(release["release_id"]), "reverify-1", "Jason")
    replay = service.reverify_release(BID_ID, str(release["release_id"]), "reverify-1", "Jason")
    assert run["processing_run_id"] == replay["processing_run_id"]
    with database._conn() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM bid_intake_processing_runs WHERE run_type='REPROCESS'"
            ).fetchone()[0]
            == 1
        )


def test_source_change_symlink_escape_and_limits_are_rejected(tmp_path: Path) -> None:
    service, repository, _database, source, managed = _service(tmp_path)
    package = source / "a.pdf"
    package.write_bytes(b"first")
    preview = service.preview(BID_ID, "landing")
    package.write_bytes(b"changed")
    with pytest.raises(SourceChangedError, match="changed after preview"):
        service.register_release(_command(preview.source_fingerprint), "Jason")
    assert repository.summary(BID_ID).releases == ()
    assert list((managed / "releases").iterdir()) == []

    package.unlink()
    target = tmp_path / "outside.txt"
    target.write_text("outside")
    os.symlink(target, source / "link.txt")
    with pytest.raises(UnsafeIntakeSourceError, match="symbolic links"):
        service.preview(BID_ID, "landing")
    with pytest.raises(UnsafeIntakeSourceError, match="allowlisted"):
        service.preview(BID_ID, "../outside")

    limited_root = tmp_path / "limited"
    limited_root.mkdir()
    limited_storage = BidPackageStorage(
        tmp_path / "limited-managed",
        {"limited": limited_root},
        max_outer_files=1,
        max_total_bytes=4,
        max_file_bytes=3,
        max_relative_path_length=5,
    )
    (limited_root / "one").write_bytes(b"1234")
    with pytest.raises(IntakeLimitError, match="individual"):
        limited_storage.preview("limited")
    (limited_root / "one").write_bytes(b"12")
    (limited_root / "two").write_bytes(b"12")
    with pytest.raises(IntakeLimitError, match="outer-file"):
        limited_storage.preview("limited")
    aggregate_storage = BidPackageStorage(
        tmp_path / "aggregate-managed",
        {"limited": limited_root},
        max_outer_files=2,
        max_total_bytes=3,
        max_file_bytes=3,
        max_relative_path_length=5,
    )
    with pytest.raises(IntakeLimitError, match="aggregate"):
        aggregate_storage.preview("limited")
    path_storage = BidPackageStorage(
        tmp_path / "path-managed",
        {"limited": limited_root},
        max_outer_files=2,
        max_total_bytes=10,
        max_file_bytes=10,
        max_relative_path_length=2,
    )
    with pytest.raises(IntakeLimitError, match="path"):
        path_storage.preview("limited")


def test_zip_is_inventoried_not_expanded_and_duplicate_occurrences_survive(tmp_path: Path) -> None:
    service, repository, database, source, _managed = _service(tmp_path)
    archive = b"PK\x03\x04synthetic-not-opened"
    (source / "package.zip").write_bytes(archive)
    first_preview = service.preview(BID_ID, "landing")
    first = service.register_release(_command(first_preview.source_fingerprint, "zip-1"), "Jason")
    first_file = repository.release_detail(BID_ID, str(first["release_id"]))["files"][0]
    assert first_file["original_filename"] == "package.zip"
    assert first_file["detected_media_type"] == "application/zip"
    with database._conn() as conn:
        assert conn.execute("SELECT count(*) FROM bid_received_files").fetchone()[0] == 1

    second_command = _command(first_preview.source_fingerprint, "zip-2").model_copy(
        update={"release_type": ReleaseType.ADDENDUM, "exact_customer_reference": "ADD-1"}
    )
    second = service.register_release(second_command, "Jason")
    second_file = repository.release_detail(BID_ID, str(second["release_id"]))["files"][0]
    assert first_file["file_id"] != second_file["file_id"]
    assert first_file["sha256"] == second_file["sha256"]
    assert repository.summary(BID_ID).duplicate_count == 1


def test_sequential_and_concurrent_registration_replay_is_idempotent(tmp_path: Path) -> None:
    service, repository, database, source, managed = _service(tmp_path)
    (source / "one.txt").write_text("synthetic")
    preview = service.preview(BID_ID, "landing")
    command = _command(preview.source_fingerprint, "same-operation")
    first = service.register_release(command, "Jason")
    second = service.register_release(command, "Jason")
    assert first["release_id"] == second["release_id"]

    (source / "one.txt").write_text("concurrent")
    concurrent_preview = service.preview(BID_ID, "landing")
    concurrent = _command(concurrent_preview.source_fingerprint, "concurrent-operation")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _index: service.register_release(concurrent, "Jason"), range(2))
        )
    assert results[0]["release_id"] == results[1]["release_id"]
    with database._conn() as conn:
        assert conn.execute("SELECT count(*) FROM bid_received_releases").fetchone()[0] == 2
        assert (
            conn.execute(
                "SELECT count(*) FROM audit_log WHERE action='bid_received_release_registered'"
            ).fetchone()[0]
            == 2
        )
    assert len(list((managed / "releases").iterdir())) == 2
    assert repository.summary(BID_ID).duplicate_count == 0


def test_filesystem_and_database_faults_leave_no_partial_release_or_orphan(tmp_path: Path) -> None:
    service, repository, database, source, managed = _service(tmp_path)
    (source / "one.txt").write_text("synthetic")
    preview = service.preview(BID_ID, "landing")
    original_publish = service.storage.publish

    def fail_publish(_staged: object) -> Path:
        raise IntakePublicationError("synthetic filesystem fault")

    service.storage.publish = fail_publish  # type: ignore[method-assign,assignment]
    with pytest.raises(IntakePublicationError, match="synthetic filesystem fault"):
        service.register_release(_command(preview.source_fingerprint, "fs-fault"), "Jason")
    service.storage.publish = original_publish  # type: ignore[method-assign]
    assert repository.summary(BID_ID).releases == ()
    assert list((managed / "releases").iterdir()) == []
    assert len(list((managed / ".quarantine").iterdir())) == 1

    with database._conn() as conn:
        conn.execute(
            """CREATE TRIGGER reject_ops11_release_audit BEFORE INSERT ON audit_log
            WHEN NEW.action='bid_received_release_registered'
            BEGIN SELECT RAISE(ABORT,'synthetic database fault'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic database fault"):
        service.register_release(_command(preview.source_fingerprint, "db-fault"), "Jason")
    assert repository.summary(BID_ID).releases == ()
    assert list((managed / "releases").iterdir()) == []


def test_failed_staging_recovery_is_quarantined_for_bounded_retention(tmp_path: Path) -> None:
    _service_instance, repository, _database, _source, managed = _service(tmp_path)
    interrupted = managed / ".staging" / "interrupted"
    interrupted.mkdir()
    (interrupted / "partial.bin").write_bytes(b"partial")
    orphan = managed / "releases" / "REL-orphan"
    orphan.mkdir()
    staged, orphans = _service_instance.storage.recover(repository.managed_release_directories())
    assert (staged, orphans) == (1, 1)
    assert list((managed / ".staging").iterdir()) == []
    assert list((managed / "releases").iterdir()) == []
    assert len(list((managed / ".quarantine").iterdir())) == 2


def test_inbox_lists_only_safe_immediate_package_folders_and_preserves_root_files(
    tmp_path: Path,
) -> None:
    service, _repository, _database, source, _managed = _service(tmp_path)
    initial = source / "Initial-Package"
    (initial / "nested").mkdir(parents=True)
    (initial / "nested" / "spec.pdf").write_bytes(b"%PDF synthetic initial")
    (source / "loose-file.txt").write_text("setup guidance only")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not intake evidence")
    (source / "unsafe-link").symlink_to(outside, target_is_directory=True)

    inbox = service.intake_inboxes(BID_ID)[0]
    assert inbox.direct_file_count == 1
    assert inbox.warning is not None
    assert [folder.display_name for folder in inbox.folders] == ["Initial-Package"]
    assert inbox.folders[0].file_count == 1
    assert inbox.folders[0].source_fingerprint is not None

    preview = service.preview(BID_ID, "landing", "Initial-Package")
    assert preview.source_folder == "Initial-Package"
    assert preview.files[0].original_relative_path == "nested/spec.pdf"
    with pytest.raises(UnsafeIntakeSourceError, match="available intake folder"):
        service.preview(BID_ID, "landing", "../outside")
    with pytest.raises(UnsafeIntakeSourceError, match="symbolic links"):
        service.preview(BID_ID, "landing", "unsafe-link")


def test_intake_root_configuration_accepts_plain_path_and_named_multiple_roots(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()

    assert parse_intake_roots(str(primary), variable_name="CONTRACTIQ_INTAKE_ROOTS") == {
        "inbox_1": primary
    }
    assert parse_intake_roots(
        '{"first":"' + primary.as_posix() + '","second":"' + secondary.as_posix() + '"}',
        variable_name="CONTRACTIQ_INTAKE_ROOTS",
    ) == {"first": primary, "second": secondary}
    with pytest.raises(ValueError, match="absolute"):
        parse_intake_roots("relative-inbox", variable_name="CONTRACTIQ_INTAKE_ROOTS")


def test_inbox_recursively_discovers_supported_package_folders_without_mutation(
    tmp_path: Path,
) -> None:
    service, _repository, database, source, _managed = _service(tmp_path)
    direct = source / "Direct-Package"
    direct.mkdir()
    (direct / "received.docx").write_bytes(b"synthetic docx")
    nested = source / "Nested-Package"
    (nested / "Commercial").mkdir(parents=True)
    (nested / "Commercial" / "Invitation-to-Bid.docx").write_bytes(b"synthetic docx")
    (nested / "Commercial" / "Duplicate-Package.zip").write_bytes(b"PK\x03\x04inert")
    (nested / "Technical").mkdir()
    (nested / "Technical" / "Register.xlsx").write_bytes(b"synthetic xlsx")
    (source / "Empty-Package").mkdir()
    unsupported = source / "Unsupported-Package"
    unsupported.mkdir()
    (unsupported / "opaque.bin").write_bytes(b"synthetic unknown")
    outside = tmp_path / "outside"
    outside.mkdir()
    (source / "Unsafe-Package").symlink_to(outside, target_is_directory=True)
    with database._conn() as conn:
        audit_before = conn.execute("SELECT count(*) FROM audit_log").fetchone()[0]
        release_before = conn.execute("SELECT count(*) FROM bid_received_releases").fetchone()[0]

    first = service.intake_inboxes(BID_ID)[0]
    assert [folder.folder_name for folder in first.folders] == [
        "Direct-Package",
        "Empty-Package",
        "Nested-Package",
        "Unsupported-Package",
    ]
    nested_folder = next(
        folder for folder in first.folders if folder.folder_name == "Nested-Package"
    )
    assert nested_folder.file_count == 3
    assert nested_folder.supported_file_count == 3
    assert nested_folder.warning is None
    assert next(
        folder for folder in first.folders if folder.folder_name == "Empty-Package"
    ).warning == ("No files found in this package folder.")
    assert next(
        folder for folder in first.folders if folder.folder_name == "Unsupported-Package"
    ).warning == ("No supported files found in this package folder.")
    preview = service.preview(BID_ID, "landing", "Nested-Package")
    assert [file.extension for file in preview.files] == [".zip", ".docx", ".xlsx"]
    assert (
        preview.files[1].detected_media_type
        and "wordprocessingml" in preview.files[1].detected_media_type
    )
    assert (
        preview.files[2].detected_media_type
        and "spreadsheetml" in preview.files[2].detected_media_type
    )
    with pytest.raises(UnsafeIntakeSourceError, match="allowlisted"):
        service.preview(BID_ID, "../landing", "Nested-Package")
    with pytest.raises(UnsafeIntakeSourceError, match="symbolic links"):
        service.preview(BID_ID, "landing", "Unsafe-Package")

    later = source / "Later-Package"
    later.mkdir()
    (later / "later.txt").write_text("new after refresh")
    refreshed = service.intake_inboxes(BID_ID)[0]
    assert [folder.folder_name for folder in refreshed.folders] == [
        "Direct-Package",
        "Empty-Package",
        "Later-Package",
        "Nested-Package",
        "Unsupported-Package",
    ]
    with database._conn() as conn:
        assert conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] == audit_before
        assert (
            conn.execute("SELECT count(*) FROM bid_received_releases").fetchone()[0]
            == release_before
        )


def test_inbox_reports_a_root_that_becomes_unavailable_without_fallback(tmp_path: Path) -> None:
    source = tmp_path / "configured-inbox"
    source.mkdir()
    storage = BidPackageStorage(tmp_path / "managed", {"landing": source})
    source.rmdir()

    inbox = storage.inbox("landing")

    assert inbox.folders == ()
    assert (
        inbox.warning
        == "Configured intake inbox is unavailable; verify configuration and permissions."
    )
