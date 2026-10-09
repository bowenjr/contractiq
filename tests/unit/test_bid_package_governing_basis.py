from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from core.bid_package_intake import (
    FileDocumentLinkCreate,
    FileDocumentRelationship,
    GoverningDisposition,
    GoverningSelectionCreate,
    GoverningSelectionItem,
    ReleaseType,
    SnapshotCreate,
)
from core.bid_package_repository import ReleaseNotIncorporableError
from core.schemas import Provenance
from tests.unit.test_bid_package_intake import (
    _bulk_command,
    _bulk_release,
    _controlled_version,
)
from tests.unit.test_bid_package_storage import BID_ID, NOW, _command


def _files(repository: object, release_id: str) -> list[dict[str, object]]:
    return repository.release_detail(BID_ID, release_id)["files"]  # type: ignore[attr-defined,no-any-return]


def _review_all_eligible(
    service: object,
    repository: object,
    release_id: str,
    *,
    operation_id: str,
) -> list[dict[str, object]]:
    files = _files(repository, release_id)
    service.bulk_classify_files(  # type: ignore[attr-defined]
        BID_ID,
        _bulk_command(release_id, files, operation_id=operation_id),
        "Jason",
    )
    return _files(repository, release_id)


def _link(
    service: object,
    release_id: str,
    file_row: dict[str, object],
    version_id: str,
    *,
    operation_id: str,
) -> None:
    service.link_file_document(  # type: ignore[attr-defined]
        BID_ID,
        str(file_row["file_id"]),
        FileDocumentLinkCreate(
            document_version_id=version_id,
            relationship=FileDocumentRelationship.EXACT_BYTES,
            operation_id=operation_id,
        ),
        "Jason",
    )


def _select_governing(
    service: object,
    repository: object,
    release_id: str,
    file_rows: list[dict[str, object]],
    *,
    disposition: GoverningDisposition = GoverningDisposition.GOVERNING,
    operation_id: str,
) -> None:
    items = []
    for row in file_rows:
        current = repository.current_governing_disposition(  # type: ignore[attr-defined]
            str(row["file_id"])
        )
        items.append(
            GoverningSelectionItem(
                file_id=str(row["file_id"]),
                supersedes_event_id=(
                    str(current["governing_event_id"])
                    if current["governing_event_id"] is not None
                    else None
                ),
            )
        )
    service.select_governing_sources(  # type: ignore[attr-defined]
        BID_ID,
        GoverningSelectionCreate(
            release_id=release_id,
            items=tuple(items),
            governing_disposition=disposition,
            operation_id=operation_id,
        ),
        "Jason",
    )


def _snapshot_documents(database: object, snapshot_id: str) -> list[dict[str, object]]:
    with database._conn() as conn:  # type: ignore[attr-defined]
        return [
            dict(row)
            for row in conn.execute(
                """SELECT * FROM bid_basis_snapshot_documents WHERE snapshot_id=?
                ORDER BY snapshot_document_id""",
                (snapshot_id,),
            ).fetchall()
        ]


def _publish_initial_governing_basis(
    tmp_path: Path,
    *,
    suffixes: tuple[str, ...],
    operation_prefix: str,
) -> tuple[
    object,
    object,
    object,
    str,
    list[dict[str, object]],
    dict[str, object],
]:
    service, repository, database, release_id = _bulk_release(tmp_path)
    files = _review_all_eligible(
        service,
        repository,
        release_id,
        operation_id=f"{operation_prefix}-review",
    )
    selected_files = files[: len(suffixes)]
    source_contents = (b"one", b"two", b"three")
    for index, (file_row, suffix) in enumerate(zip(selected_files, suffixes, strict=True)):
        _document_id, version_id = _controlled_version(
            database,
            source_contents[index],
            suffix=suffix,
        )
        _link(
            service,
            release_id,
            file_row,
            version_id,
            operation_id=f"{operation_prefix}-link-{index}",
        )
    _select_governing(
        service,
        repository,
        release_id,
        selected_files,
        operation_id=f"{operation_prefix}-select",
    )
    snapshot = service.publish_basis(  # type: ignore[attr-defined]
        BID_ID,
        SnapshotCreate(
            release_ids=(release_id,),
            label=f"{operation_prefix} initial basis",
            operation_id=f"{operation_prefix}-basis-1",
        ),
        "Jason",
    )
    return service, repository, database, release_id, selected_files, snapshot


def test_analysis_only_linked_file_does_not_create_bid_basis(tmp_path: Path) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    files = _review_all_eligible(
        service, repository, release_id, operation_id="review-analysis-only"
    )
    _document_id, version_id = _controlled_version(database, b"one", suffix="000000000101")
    _link(
        service,
        release_id,
        files[0],
        version_id,
        operation_id="link-analysis-only",
    )

    assert repository.incorporation_issues(BID_ID, release_id) == ()
    assert repository.candidate_snapshot_document_count(BID_ID, (release_id,)) == 0
    with pytest.raises(
        ReleaseNotIncorporableError,
        match="at least one exact controlled document version",
    ):
        service.publish_basis(
            BID_ID,
            SnapshotCreate(
                release_ids=(release_id,),
                label="Analysis-only package",
                operation_id="basis-analysis-only",
            ),
            "Jason",
        )


def test_only_governing_linked_file_enters_mixed_bid_basis(tmp_path: Path) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    files = _review_all_eligible(service, repository, release_id, operation_id="review-mixed")
    governing_document_id, governing_version_id = _controlled_version(
        database, b"one", suffix="000000000102"
    )
    _analysis_document_id, analysis_version_id = _controlled_version(
        database, b"two", suffix="000000000103"
    )
    _link(
        service,
        release_id,
        files[0],
        governing_version_id,
        operation_id="link-mixed-governing",
    )
    _link(
        service,
        release_id,
        files[1],
        analysis_version_id,
        operation_id="link-mixed-analysis",
    )
    _select_governing(
        service,
        repository,
        release_id,
        [files[0]],
        operation_id="select-mixed-governing",
    )

    assert repository.incorporation_issues(BID_ID, release_id) == ()
    candidate_count = repository.candidate_snapshot_document_count(BID_ID, (release_id,))
    assert candidate_count == 1
    snapshot = service.publish_basis(
        BID_ID,
        SnapshotCreate(
            release_ids=(release_id,),
            label="Explicit governing basis",
            operation_id="basis-mixed",
        ),
        "Jason",
    )
    documents = _snapshot_documents(database, str(snapshot["snapshot_id"]))
    assert len(documents) == candidate_count
    assert [(row["document_id"], row["source_received_file_id"]) for row in documents] == [
        (governing_document_id, files[0]["file_id"])
    ]


def test_governing_file_without_controlled_link_blocks_publication(tmp_path: Path) -> None:
    service, repository, _database, release_id = _bulk_release(tmp_path)
    files = _review_all_eligible(
        service, repository, release_id, operation_id="review-unlinked-governing"
    )
    _select_governing(
        service,
        repository,
        release_id,
        [files[0]],
        operation_id="select-unlinked-governing",
    )

    assert any(
        f"governing file {files[0]['file_id']}" in issue
        for issue in repository.incorporation_issues(BID_ID, release_id)
    )
    assert repository.candidate_snapshot_document_count(BID_ID, (release_id,)) == 0
    with pytest.raises(ReleaseNotIncorporableError, match="governing file"):
        service.publish_basis(
            BID_ID,
            SnapshotCreate(
                release_ids=(release_id,),
                label="Unlinked governing source",
                operation_id="basis-unlinked-governing",
            ),
            "Jason",
        )


def test_not_governing_supersession_removes_file_from_new_snapshot(tmp_path: Path) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    files = _review_all_eligible(
        service, repository, release_id, operation_id="review-superseded-governing"
    )
    first_document_id, first_version_id = _controlled_version(
        database, b"one", suffix="000000000104"
    )
    _second_document_id, second_version_id = _controlled_version(
        database, b"two", suffix="000000000105"
    )
    _link(
        service,
        release_id,
        files[0],
        first_version_id,
        operation_id="link-still-governing",
    )
    _link(
        service,
        release_id,
        files[1],
        second_version_id,
        operation_id="link-no-longer-governing",
    )
    _select_governing(
        service,
        repository,
        release_id,
        files[:2],
        operation_id="select-two-governing",
    )
    _select_governing(
        service,
        repository,
        release_id,
        [files[1]],
        disposition=GoverningDisposition.NOT_GOVERNING,
        operation_id="deselect-second-governing",
    )

    candidate_count = repository.candidate_snapshot_document_count(BID_ID, (release_id,))
    assert candidate_count == 1
    snapshot = service.publish_basis(
        BID_ID,
        SnapshotCreate(
            release_ids=(release_id,),
            label="Superseded governing decision",
            operation_id="basis-superseded-governing",
        ),
        "Jason",
    )
    documents = _snapshot_documents(database, str(snapshot["snapshot_id"]))
    assert len(documents) == candidate_count
    assert [row["document_id"] for row in documents] == [first_document_id]


def test_unselected_inherited_source_is_dropped_without_mutating_prior_snapshot(
    tmp_path: Path,
) -> None:
    service, repository, database, release_id, files, first_snapshot = (
        _publish_initial_governing_basis(
            tmp_path,
            suffixes=("000000000109", "000000000110"),
            operation_prefix="next-basis-unselect",
        )
    )
    first_snapshot_id = str(first_snapshot["snapshot_id"])
    first_documents = _snapshot_documents(database, first_snapshot_id)
    first_by_source = {str(row["source_received_file_id"]): row for row in first_documents}
    with database._conn() as conn:  # type: ignore[attr-defined]
        first_snapshot_row = dict(
            conn.execute(
                "SELECT * FROM bid_basis_snapshots WHERE snapshot_id=?",
                (first_snapshot_id,),
            ).fetchone()
        )
        first_release_rows = [
            dict(row)
            for row in conn.execute(
                """SELECT * FROM bid_basis_snapshot_releases WHERE snapshot_id=?
                ORDER BY incorporation_sequence""",
                (first_snapshot_id,),
            ).fetchall()
        ]
    retained_file_id = str(files[0]["file_id"])
    unselected_file_id = str(files[1]["file_id"])

    _select_governing(
        service,
        repository,
        release_id,
        [files[1]],
        disposition=GoverningDisposition.NOT_GOVERNING,
        operation_id="next-basis-unselect-second",
    )
    second_snapshot = service.publish_basis(  # type: ignore[attr-defined]
        BID_ID,
        SnapshotCreate(
            expected_current_snapshot_id=first_snapshot_id,
            release_ids=(release_id,),
            label="Basis after explicit unselection",
            operation_id="next-basis-unselect-basis-2",
        ),
        "Jason",
    )

    second_documents = _snapshot_documents(database, str(second_snapshot["snapshot_id"]))
    with database._conn() as conn:  # type: ignore[attr-defined]
        first_snapshot_row_after = dict(
            conn.execute(
                "SELECT * FROM bid_basis_snapshots WHERE snapshot_id=?",
                (first_snapshot_id,),
            ).fetchone()
        )
        first_release_rows_after = [
            dict(row)
            for row in conn.execute(
                """SELECT * FROM bid_basis_snapshot_releases WHERE snapshot_id=?
                ORDER BY incorporation_sequence""",
                (first_snapshot_id,),
            ).fetchall()
        ]
    assert first_snapshot_row_after == first_snapshot_row
    assert first_release_rows_after == first_release_rows
    assert _snapshot_documents(database, first_snapshot_id) == first_documents
    assert unselected_file_id in first_by_source
    assert {str(row["source_received_file_id"]) for row in second_documents} == {retained_file_id}
    assert (
        second_documents[0]["document_version_id"]
        == first_by_source[retained_file_id]["document_version_id"]
    )


def test_reselecting_source_after_unselection_carries_it_into_next_snapshot(
    tmp_path: Path,
) -> None:
    service, repository, database, release_id, files, first_snapshot = (
        _publish_initial_governing_basis(
            tmp_path,
            suffixes=("000000000111",),
            operation_prefix="next-basis-reselect",
        )
    )
    first_snapshot_id = str(first_snapshot["snapshot_id"])
    first_documents = _snapshot_documents(database, first_snapshot_id)
    source_file_id = str(files[0]["file_id"])
    _select_governing(
        service,
        repository,
        release_id,
        files,
        disposition=GoverningDisposition.NOT_GOVERNING,
        operation_id="next-basis-reselect-unselect",
    )
    _select_governing(
        service,
        repository,
        release_id,
        files,
        operation_id="next-basis-reselect-governing",
    )

    assert (
        repository.current_governing_disposition(source_file_id)["governing_disposition"]
        == GoverningDisposition.GOVERNING.value
    )  # type: ignore[attr-defined]
    second_snapshot = service.publish_basis(  # type: ignore[attr-defined]
        BID_ID,
        SnapshotCreate(
            expected_current_snapshot_id=first_snapshot_id,
            release_ids=(release_id,),
            label="Basis after governing re-selection",
            operation_id="next-basis-reselect-basis-2",
        ),
        "Jason",
    )
    second_documents = _snapshot_documents(database, str(second_snapshot["snapshot_id"]))

    assert _snapshot_documents(database, first_snapshot_id) == first_documents
    assert len(second_documents) == 1
    assert second_documents[0]["source_received_file_id"] == source_file_id
    assert second_documents[0]["document_version_id"] == first_documents[0]["document_version_id"]


def test_inherited_source_without_governing_event_remains_in_next_snapshot(
    tmp_path: Path,
) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    files = _review_all_eligible(
        service,
        repository,
        release_id,
        operation_id="legacy-unset-review",
    )
    document_id, version_id = _controlled_version(
        database,
        b"one",
        suffix="000000000112",
    )
    _link(
        service,
        release_id,
        files[0],
        version_id,
        operation_id="legacy-unset-link",
    )
    source_file_id = str(files[0]["file_id"])
    assert repository.current_governing_disposition(source_file_id) == {  # type: ignore[attr-defined]
        "file_id": source_file_id,
        "governing_event_id": None,
        "governing_disposition": GoverningDisposition.UNSET.value,
    }
    provenance = Provenance.from_human("Jason").model_dump_json()
    first_snapshot_id = "BBS-legacy-unset"
    with database._conn() as conn:  # type: ignore[attr-defined]
        conn.execute(
            "INSERT INTO bid_basis_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                first_snapshot_id,
                BID_ID,
                1,
                None,
                "Pre-governing Bid Basis",
                None,
                hashlib.sha256(b"legacy-unset-snapshot").hexdigest(),
                "legacy-unset-basis-1",
                "Jason",
                NOW.isoformat(),
                provenance,
            ),
        )
        conn.execute(
            "INSERT INTO bid_basis_snapshot_releases VALUES(?,?,?,?,?,?,?)",
            (
                "BBSR-legacy-unset",
                first_snapshot_id,
                release_id,
                1,
                "Jason",
                NOW.isoformat(),
                provenance,
            ),
        )
        conn.execute(
            "INSERT INTO bid_basis_snapshot_documents VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "BBSD-legacy-unset",
                first_snapshot_id,
                document_id,
                version_id,
                release_id,
                source_file_id,
                "GOVERNING",
                "Jason",
                NOW.isoformat(),
                provenance,
            ),
        )
    first_documents = _snapshot_documents(database, first_snapshot_id)

    second_snapshot = service.publish_basis(  # type: ignore[attr-defined]
        BID_ID,
        SnapshotCreate(
            expected_current_snapshot_id=first_snapshot_id,
            release_ids=(release_id,),
            label="Basis retaining legacy UNSET source",
            operation_id="legacy-unset-basis-2",
        ),
        "Jason",
    )
    second_documents = _snapshot_documents(database, str(second_snapshot["snapshot_id"]))

    assert _snapshot_documents(database, first_snapshot_id) == first_documents
    assert len(second_documents) == 1
    assert second_documents[0]["source_received_file_id"] == source_file_id
    assert second_documents[0]["document_version_id"] == version_id


def test_candidate_count_matches_next_snapshot_after_source_unselection(
    tmp_path: Path,
) -> None:
    service, repository, database, release_id, files, first_snapshot = (
        _publish_initial_governing_basis(
            tmp_path,
            suffixes=("000000000113", "000000000114"),
            operation_prefix="next-basis-count",
        )
    )
    first_snapshot_id = str(first_snapshot["snapshot_id"])
    _select_governing(
        service,
        repository,
        release_id,
        [files[1]],
        disposition=GoverningDisposition.NOT_GOVERNING,
        operation_id="next-basis-count-unselect",
    )

    candidate_count = repository.candidate_snapshot_document_count(  # type: ignore[attr-defined]
        BID_ID, (release_id,)
    )
    second_snapshot = service.publish_basis(  # type: ignore[attr-defined]
        BID_ID,
        SnapshotCreate(
            expected_current_snapshot_id=first_snapshot_id,
            release_ids=(release_id,),
            label="Basis matching candidate count",
            operation_id="next-basis-count-basis-2",
        ),
        "Jason",
    )
    second_documents = _snapshot_documents(database, str(second_snapshot["snapshot_id"]))

    assert candidate_count == len(second_documents) == 1


def test_unselecting_only_inherited_source_blocks_empty_next_snapshot(
    tmp_path: Path,
) -> None:
    service, repository, _database, release_id, files, first_snapshot = (
        _publish_initial_governing_basis(
            tmp_path,
            suffixes=("000000000115",),
            operation_prefix="next-basis-empty",
        )
    )
    first_snapshot_id = str(first_snapshot["snapshot_id"])
    _select_governing(
        service,
        repository,
        release_id,
        files,
        disposition=GoverningDisposition.NOT_GOVERNING,
        operation_id="next-basis-empty-unselect",
    )

    with pytest.raises(
        ReleaseNotIncorporableError,
        match="at least one exact controlled document version",
    ):
        service.publish_basis(  # type: ignore[attr-defined]
            BID_ID,
            SnapshotCreate(
                expected_current_snapshot_id=first_snapshot_id,
                release_ids=(release_id,),
                label="Invalid empty next basis",
                operation_id="next-basis-empty-basis-2",
            ),
            "Jason",
        )


def test_cross_bid_controlled_version_link_is_still_invalid(tmp_path: Path) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    files = _review_all_eligible(
        service, repository, release_id, operation_id="review-cross-bid-link"
    )
    existing_bid = service.bid_repository.get_bid(BID_ID)
    assert existing_bid is not None
    other_bid_id = "B-2026-0099"
    service.bid_repository.create_bid(existing_bid.model_copy(update={"bid_id": other_bid_id}))
    provenance = Provenance.from_human("Jason").model_dump_json()
    document_id = "DOC-00000000-0000-0000-0000-000000000106"
    version_id = "DV-00000000-0000-0000-0000-000000000106"
    with database._conn() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """INSERT INTO documents(id,filename,bid_id,control_managed,control_title,
            document_number,document_category,control_lifecycle,current_version_id,
            control_created_at,control_updated_at,control_version,control_provenance_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                document_id,
                "other-bid.pdf",
                other_bid_id,
                1,
                "Other Bid controlled source",
                "OTHER-001",
                "SOLICITATION",
                "ACTIVE",
                version_id,
                NOW.isoformat(),
                NOW.isoformat(),
                1,
                provenance,
            ),
        )
        conn.execute(
            """INSERT INTO document_versions(document_version_id,document_id,version_label,
            issued_date,received_at,original_filename,media_type,byte_size,sha256_digest,
            storage_key,predecessor_version_id,version_state,created_at,provenance_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                version_id,
                document_id,
                "A",
                "2026-09-10",
                NOW.isoformat(),
                "other-bid.pdf",
                "application/pdf",
                3,
                hashlib.sha256(b"one").hexdigest(),
                "versions/other/cross-bid.bin",
                None,
                "CURRENT",
                NOW.isoformat(),
                provenance,
            ),
        )
        conn.execute(
            """INSERT INTO bid_received_file_document_links VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                "FDL-cross-bid",
                files[0]["file_id"],
                version_id,
                "EXACT_BYTES",
                None,
                "link-cross-bid",
                "Jason",
                NOW.isoformat(),
                provenance,
            ),
        )
    _select_governing(
        service,
        repository,
        release_id,
        [files[0]],
        operation_id="select-cross-bid-link",
    )

    assert any(
        "invalid controlled-version link" in issue
        for issue in repository.incorporation_issues(BID_ID, release_id)
    )
    with pytest.raises(ReleaseNotIncorporableError, match="invalid controlled-version link"):
        service.publish_basis(
            BID_ID,
            SnapshotCreate(
                release_ids=(release_id,),
                label="Cross-Bid invalid link",
                operation_id="basis-cross-bid-link",
            ),
            "Jason",
        )


def test_addendum_snapshot_preserves_first_snapshot_rows_and_hash(tmp_path: Path) -> None:
    service, repository, database, first_release_id = _bulk_release(tmp_path)
    first_files = _review_all_eligible(
        service, repository, first_release_id, operation_id="review-first-release"
    )
    _first_document_id, first_version_id = _controlled_version(
        database, b"one", suffix="000000000107"
    )
    _link(
        service,
        first_release_id,
        first_files[0],
        first_version_id,
        operation_id="link-first-release",
    )
    _select_governing(
        service,
        repository,
        first_release_id,
        [first_files[0]],
        operation_id="select-first-release",
    )
    first_candidate_count = repository.candidate_snapshot_document_count(
        BID_ID, (first_release_id,)
    )
    first_snapshot = service.publish_basis(
        BID_ID,
        SnapshotCreate(
            release_ids=(first_release_id,),
            label="Initial governing basis",
            operation_id="basis-first-release",
        ),
        "Jason",
    )
    first_snapshot_id = str(first_snapshot["snapshot_id"])
    first_documents_before = _snapshot_documents(database, first_snapshot_id)
    assert len(first_documents_before) == first_candidate_count
    with database._conn() as conn:  # type: ignore[attr-defined]
        first_row_before = dict(
            conn.execute(
                "SELECT * FROM bid_basis_snapshots WHERE snapshot_id=?",
                (first_snapshot_id,),
            ).fetchone()
        )

    source = service.storage.source_roots["landing"]
    for path in source.rglob("*"):
        if path.is_file():
            path.unlink()
    (source / "addendum.pdf").write_bytes(b"addendum")
    preview = service.preview(BID_ID, "landing")
    addendum = service.register_release(
        _command(preview.source_fingerprint, "register-addendum-basis").model_copy(
            update={
                "release_type": ReleaseType.ADDENDUM,
                "exact_customer_reference": "ADD-BASIS-1",
            }
        ),
        "Jason",
    )
    addendum_release_id = str(addendum["release_id"])
    addendum_files = _review_all_eligible(
        service,
        repository,
        addendum_release_id,
        operation_id="review-addendum-release",
    )
    _addendum_document_id, addendum_version_id = _controlled_version(
        database, b"addendum", suffix="000000000108", version_label="B"
    )
    _link(
        service,
        addendum_release_id,
        addendum_files[0],
        addendum_version_id,
        operation_id="link-addendum-release",
    )
    _select_governing(
        service,
        repository,
        addendum_release_id,
        [addendum_files[0]],
        operation_id="select-addendum-release",
    )

    release_ids = (first_release_id, addendum_release_id)
    second_candidate_count = repository.candidate_snapshot_document_count(BID_ID, release_ids)
    second_snapshot = service.publish_basis(
        BID_ID,
        SnapshotCreate(
            expected_current_snapshot_id=first_snapshot_id,
            release_ids=release_ids,
            label="Addendum governing basis",
            operation_id="basis-addendum-release",
        ),
        "Jason",
    )
    second_documents = _snapshot_documents(database, str(second_snapshot["snapshot_id"]))
    assert len(second_documents) == second_candidate_count == 2
    with database._conn() as conn:  # type: ignore[attr-defined]
        first_row_after = dict(
            conn.execute(
                "SELECT * FROM bid_basis_snapshots WHERE snapshot_id=?",
                (first_snapshot_id,),
            ).fetchone()
        )
    assert first_row_after == first_row_before
    assert first_row_after["snapshot_hash"] == first_row_before["snapshot_hash"]
    assert _snapshot_documents(database, first_snapshot_id) == first_documents_before


def test_not_assessed_file_still_blocks_publication(tmp_path: Path) -> None:
    service, repository, _database, release_id = _bulk_release(tmp_path)

    assert any(
        "has not been reviewed" in issue
        for issue in repository.incorporation_issues(BID_ID, release_id)
    )
    assert repository.candidate_snapshot_document_count(BID_ID, (release_id,)) == 0
    with pytest.raises(ReleaseNotIncorporableError, match="has not been reviewed"):
        service.publish_basis(
            BID_ID,
            SnapshotCreate(
                release_ids=(release_id,),
                label="Unreviewed package",
                operation_id="basis-unreviewed-package",
            ),
            "Jason",
        )
