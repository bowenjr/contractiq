"""Browser folder-picker intake: private staging, validation, and reuse of the existing
OPS-11 preview/confirm/publish pipeline. No real customer files are used — every upload is
synthetic bytes built at test time."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from core.bid_package_intake import ReleaseChannel, ReleaseRegistration, ReleaseType
from core.bid_package_storage import (
    BROWSER_UPLOAD_ALIAS,
    IntakeLimitError,
    SourceChangedError,
    UnsafeIntakeSourceError,
)
from tests.unit.test_bid_package_storage import BID_ID, NOW, _service


def _uploads(pairs: list[tuple[str, bytes]]) -> list[tuple[str, io.BytesIO]]:
    return [(name, io.BytesIO(content)) for name, content in pairs]


def test_browser_upload_preserves_relative_paths_and_hashes(tmp_path: Path) -> None:
    service, _repository, _database, _source, managed = _service(tmp_path)
    uploads = _uploads(
        [
            ("Test2 Bid/RFP.pdf", b"%PDF fake rfp content"),
            ("Test2 Bid/Attachments/Drawing.pdf", b"%PDF fake drawing content"),
        ]
    )
    preview = service.stage_browser_upload(BID_ID, uploads)
    assert preview.source_root_alias == BROWSER_UPLOAD_ALIAS
    assert preview.file_count == 2
    paths = {f.original_relative_path for f in preview.files}
    assert paths == {"RFP.pdf", "Attachments/Drawing.pdf"}
    for item in preview.files:
        assert len(item.sha256) == 64


def test_browser_upload_rejects_path_traversal_and_absolute_paths(tmp_path: Path) -> None:
    service, _repository, _database, _source, _managed = _service(tmp_path)
    for bad_path in ("Test2 Bid/../../etc/passwd", "/etc/passwd", "Test2 Bid/..\\..\\evil"):
        with pytest.raises((UnsafeIntakeSourceError, IntakeLimitError)):
            service.stage_browser_upload(BID_ID, _uploads([(bad_path, b"x" * 20)]))


def test_browser_upload_rejects_mismatched_top_folder(tmp_path: Path) -> None:
    service, _repository, _database, _source, _managed = _service(tmp_path)
    with pytest.raises(UnsafeIntakeSourceError):
        service.stage_browser_upload(
            BID_ID,
            _uploads(
                [
                    ("Test2 Bid/a.pdf", b"content-a"),
                    ("Other Folder/b.pdf", b"content-b"),
                ]
            ),
        )


def test_interrupted_upload_leaves_no_staging_directory(tmp_path: Path) -> None:
    service, _repository, _database, _source, managed = _service(tmp_path)
    storage = service.storage

    class _ExplodingStream(io.BytesIO):
        def read(self, *args: object) -> bytes:
            raise ConnectionError("simulated interrupted upload")

    before = set(storage.browser_uploads_root.iterdir())
    with pytest.raises(ConnectionError):
        service.stage_browser_upload(BID_ID, [("Test2 Bid/a.pdf", _ExplodingStream(b"never read"))])
    after = set(storage.browser_uploads_root.iterdir())
    assert before == after  # no orphaned token directory


def test_oversized_file_is_rejected_and_cleaned_up(tmp_path: Path) -> None:
    service, _repository, _database, _source, managed = _service(tmp_path, max_file=100)
    storage = service.storage
    before = set(storage.browser_uploads_root.iterdir())
    with pytest.raises(IntakeLimitError):
        service.stage_browser_upload(BID_ID, _uploads([("Test2 Bid/big.pdf", b"x" * 200)]))
    after = set(storage.browser_uploads_root.iterdir())
    assert before == after


def test_cross_bid_staging_token_is_rejected(tmp_path: Path) -> None:
    service, repository, database, source, managed = _service(tmp_path)
    from datetime import date

    from core.bid_repository import BidRepository
    from core.enums import BidLevel, BidStatus, CustomerType, Gate
    from core.schemas import Bid

    other_bid_id = "B-2026-0099"
    bid_repo = BidRepository(database)
    bid_repo.create_bid(
        Bid(
            bid_id=other_bid_id,
            customer="Other Co",
            customer_type=CustomerType.EPC,
            project_name="Other Bid",
            sales_owner="Sales",
            bc_owner="Jason",
            release_date=date(2026, 9, 1),
            customer_due_date=date(2026, 10, 1),
            internal_due_date=date(2026, 9, 25),
            estimated_value=0,
            classification=BidLevel.LEVEL_0,
            current_gate=Gate.G0,
            status=BidStatus.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    preview = service.stage_browser_upload(BID_ID, _uploads([("Test2 Bid/a.pdf", b"content")]))
    token = preview.source_folder
    with pytest.raises(UnsafeIntakeSourceError):
        service.preview(other_bid_id, BROWSER_UPLOAD_ALIAS, token)


def test_full_browser_upload_journey_preview_and_confirm(tmp_path: Path) -> None:
    service, repository, database, source, managed = _service(tmp_path)
    preview = service.stage_browser_upload(
        BID_ID,
        _uploads(
            [
                ("Test2 Bid/RFP.pdf", b"%PDF instructions to bidders"),
                ("Test2 Bid/Pricing/Schedule.xlsx", b"fake xlsx bytes"),
            ]
        ),
    )
    registration = ReleaseRegistration(
        bid_id=BID_ID,
        release_type=ReleaseType.INITIAL_PACKAGE,
        exact_customer_reference="Test2 Bid initial package",
        customer_issue_date=NOW.date(),
        received_at=NOW,
        received_channel=ReleaseChannel.EMAIL,
        source_root_alias=preview.source_root_alias,
        source_folder=preview.source_folder,
        expected_source_fingerprint=preview.source_fingerprint,
        operation_id="browser-op-1",
    )
    release = service.register_release(registration, "Jason")
    assert release["source_fingerprint"] == preview.source_fingerprint

    detail = repository.release_detail(BID_ID, str(release["release_id"]))
    stored_paths = {f["original_relative_path"] for f in detail["files"]}
    assert stored_paths == {"RFP.pdf", "Pricing/Schedule.xlsx"}
    stored_sizes = {f["original_relative_path"]: f["byte_size"] for f in detail["files"]}
    assert stored_sizes["RFP.pdf"] == len(b"%PDF instructions to bidders")

    # bytes are safely duplicated into the release's own managed storage — the ephemeral
    # browser-upload staging directory is discarded, not left as a second copy.
    assert not (service.storage.browser_uploads_root / preview.source_folder).exists()


def test_duplicate_confirmation_is_idempotent_and_cleans_up_once(tmp_path: Path) -> None:
    service, repository, database, source, managed = _service(tmp_path)
    preview = service.stage_browser_upload(BID_ID, _uploads([("Test2 Bid/a.pdf", b"content-a")]))
    registration = ReleaseRegistration(
        bid_id=BID_ID,
        release_type=ReleaseType.INITIAL_PACKAGE,
        exact_customer_reference="Test2 Bid",
        customer_issue_date=NOW.date(),
        received_at=NOW,
        received_channel=ReleaseChannel.EMAIL,
        source_root_alias=preview.source_root_alias,
        source_folder=preview.source_folder,
        expected_source_fingerprint=preview.source_fingerprint,
        operation_id="browser-op-dup",
    )
    first = service.register_release(registration, "Jason")
    second = service.register_release(registration, "Jason")
    assert first["release_id"] == second["release_id"]
    releases = repository.summary(BID_ID).releases
    assert len(releases) == 1


def test_confirmation_failure_leaves_no_partial_release(tmp_path: Path) -> None:
    service, repository, database, source, managed = _service(tmp_path)
    preview = service.stage_browser_upload(BID_ID, _uploads([("Test2 Bid/a.pdf", b"content-a")]))
    registration = ReleaseRegistration(
        bid_id=BID_ID,
        release_type=ReleaseType.INITIAL_PACKAGE,
        exact_customer_reference="Test2 Bid",
        customer_issue_date=NOW.date(),
        received_at=NOW,
        received_channel=ReleaseChannel.EMAIL,
        source_root_alias=preview.source_root_alias,
        source_folder=preview.source_folder,
        expected_source_fingerprint="0" * 64,  # deliberately wrong — simulates tampering
        operation_id="browser-op-fail",
    )
    with pytest.raises(SourceChangedError):
        service.register_release(registration, "Jason")
    releases = repository.summary(BID_ID).releases
    assert releases == ()
    # the browser upload itself is preserved so the user can retry confirmation
    assert (service.storage.browser_uploads_root / preview.source_folder).exists()


def test_addendum_mode_reuses_the_same_browser_upload_pipeline(tmp_path: Path) -> None:
    service, repository, database, source, managed = _service(tmp_path)
    preview = service.stage_browser_upload(
        BID_ID, _uploads([("Addendum 1/Revised-RFP.pdf", b"revised content")])
    )
    registration = ReleaseRegistration(
        bid_id=BID_ID,
        release_type=ReleaseType.ADDENDUM,
        exact_customer_reference="Addendum 1",
        customer_issue_date=NOW.date(),
        received_at=NOW,
        received_channel=ReleaseChannel.EMAIL,
        source_root_alias=preview.source_root_alias,
        source_folder=preview.source_folder,
        expected_source_fingerprint=preview.source_fingerprint,
        operation_id="browser-op-addendum",
    )
    release = service.register_release(registration, "Jason")
    assert release["release_type"] == "ADDENDUM"
