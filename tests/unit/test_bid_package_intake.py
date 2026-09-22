from __future__ import annotations

import hashlib
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.bid_package_intake import (
    AcknowledgementCreate,
    AcknowledgementEventType,
    AnalysisEligibility,
    BulkFileDispositionCreate,
    BulkFileReviewItem,
    ClassificationMethod,
    ContentForm,
    DirectiveCreate,
    DirectiveDispositionCreate,
    DirectiveDispositionStatus,
    DirectiveMateriality,
    FileDispositionCreate,
    FileDocumentLinkCreate,
    FileDocumentRelationship,
    NoticeExpectation,
    ReleaseChannel,
    ReleaseNoticeCreate,
    ReleaseType,
    SnapshotCreate,
)
from core.bid_package_repository import (
    OPS_11_MIGRATION_ID,
    BidPackageRepository,
    IntakeNotFoundError,
    ReleaseNotIncorporableError,
    StaleIntakeError,
)
from core.bid_package_service import BidPackageService
from core.bid_package_storage import BidPackageStorage
from core.proposal_exchange_service import ProposalExchangeService
from core.schemas import Provenance
from core.work_item_repository import WorkItemRepository
from core.work_item_service import MyDayService, WorkItemService
from tests.unit.test_bid_package_storage import BID_ID, NOW, _command, _service
from tests.unit.test_ops09 import BID_ID as OPS09_BID_ID
from tests.unit.test_ops09 import service_with_ready_bid


def _controlled_version(
    database: object,
    content: bytes,
    *,
    suffix: str = "000000000011",
    version_label: str = "A",
) -> tuple[str, str]:
    document_id = f"DOC-00000000-0000-0000-0000-{suffix}"
    version_id = f"DV-00000000-0000-0000-0000-{suffix}"
    provenance = Provenance.from_human("Jason").model_dump_json()
    with database._conn() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """INSERT INTO documents(id,filename,bid_id,control_managed,control_title,
            document_number,document_category,control_lifecycle,current_version_id,
            control_created_at,control_updated_at,control_version,control_provenance_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                document_id,
                "synthetic.pdf",
                BID_ID,
                1,
                "Synthetic controlled source",
                "SYN-001",
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
                version_label,
                date(2026, 9, 10).isoformat(),
                NOW.isoformat(),
                "synthetic.pdf",
                "application/pdf",
                len(content),
                hashlib.sha256(content).hexdigest(),
                f"versions/{suffix[:2]}/{version_id}.upal",
                None,
                "CURRENT",
                NOW.isoformat(),
                provenance,
            ),
        )
    return document_id, version_id


def _review_and_link(
    service: object,
    repository: object,
    release_id: str,
    version_id: str,
    *,
    relationship: FileDocumentRelationship = FileDocumentRelationship.EXACT_BYTES,
    bid_id: str = BID_ID,
) -> str:
    detail = repository.release_detail(bid_id, release_id)  # type: ignore[attr-defined]
    file_row = detail["files"][0]
    file_id = str(file_row["file_id"])
    service.classify_file(  # type: ignore[attr-defined]
        bid_id,
        file_id,
        FileDispositionCreate(
            content_form=ContentForm.TEXTUAL,
            classification_method=ClassificationMethod.HUMAN_REVIEW,
            confidence=1,
            analysis_eligibility=AnalysisEligibility.ELIGIBLE,
            supersedes_event_id=str(file_row["disposition_event_id"]),
            operation_id=f"classify-{release_id}",
        ),
        "Jason",
    )
    service.link_file_document(  # type: ignore[attr-defined]
        bid_id,
        file_id,
        FileDocumentLinkCreate(
            document_version_id=version_id,
            relationship=relationship,
            operation_id=f"link-{release_id}",
        ),
        "Jason",
    )
    return file_id


def _bulk_command(
    release_id: str,
    files: list[dict[str, object]],
    *,
    eligibility: AnalysisEligibility = AnalysisEligibility.ELIGIBLE,
    content_form: ContentForm | None = None,
    operation_id: str = "bulk-review-1",
) -> BulkFileDispositionCreate:
    return BulkFileDispositionCreate(
        release_id=release_id,
        items=tuple(
            BulkFileReviewItem(
                file_id=str(row["file_id"]),
                supersedes_event_id=str(row["disposition_event_id"]),
            )
            for row in files
        ),
        analysis_eligibility=eligibility,
        content_form=content_form,
        exclusion_reason="Not used in this offer"
        if eligibility is AnalysisEligibility.EXCLUDED
        else None,
        operation_id=operation_id,
    )


def _bulk_release(tmp_path: Path) -> tuple[BidPackageService, BidPackageRepository, object, str]:
    service, repository, database, source, _managed = _service(tmp_path)
    (source / "Part A").mkdir()
    (source / "Part A" / "one.pdf").write_bytes(b"one")
    (source / "Part A" / "two.xlsx").write_bytes(b"two")
    (source / "Part B").mkdir()
    (source / "Part B" / "three.docx").write_bytes(b"three")
    (source / "root.txt").write_bytes(b"root")
    preview = service.preview(BID_ID, "landing")
    release = service.register_release(
        _command(preview.source_fingerprint, "bulk-release-1"), "Jason"
    )
    return service, repository, database, str(release["release_id"])


def test_bulk_review_is_atomic_and_leaves_unselected_files_untouched(tmp_path: Path) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    before = repository.release_detail(BID_ID, release_id)["files"]
    selected = before[:2]
    result = service.bulk_classify_files(
        BID_ID,
        _bulk_command(release_id, selected, operation_id="bulk-success"),
        "Jason",
    )
    assert len(result) == 2
    after = repository.release_detail(BID_ID, release_id)["files"]
    assert [row["analysis_eligibility"] for row in after[:2]] == ["ELIGIBLE", "ELIGIBLE"]
    assert after[2]["analysis_eligibility"] == "NOT_ASSESSED"
    # No content assertion is inferred from .pdf, .xlsx, or .docx extensions.
    assert [row["content_form"] for row in after[:2]] == ["UNKNOWN", "UNKNOWN"]
    with database._conn() as conn:  # type: ignore[attr-defined]
        events = conn.execute(
            "SELECT count(*) FROM bid_received_file_disposition_events"
        ).fetchone()[0]
        audits = conn.execute(
            "SELECT count(*) FROM audit_log WHERE action='bid_received_file_disposition_recorded'"
        ).fetchone()[0]
    assert events == 6  # Four safe defaults plus two immutable replacements.
    assert audits == 2  # One immutable disposition audit per changed file.


def test_bulk_review_rejects_stale_cross_release_or_duplicate_selection_without_changes(
    tmp_path: Path,
) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    before = repository.release_detail(BID_ID, release_id)["files"]
    service.classify_file(
        BID_ID,
        str(before[0]["file_id"]),
        FileDispositionCreate(
            content_form=ContentForm.UNKNOWN,
            classification_method=ClassificationMethod.HUMAN_REVIEW,
            analysis_eligibility=AnalysisEligibility.ELIGIBLE,
            supersedes_event_id=str(before[0]["disposition_event_id"]),
            operation_id="make-token-stale",
        ),
        "Jason",
    )
    with database._conn() as conn:  # type: ignore[attr-defined]
        count_before = conn.execute(
            "SELECT count(*) FROM bid_received_file_disposition_events"
        ).fetchone()[0]
    with pytest.raises(StaleIntakeError, match="selected files changed"):
        service.bulk_classify_files(BID_ID, _bulk_command(release_id, before[:2]), "Jason")
    with database._conn() as conn:  # type: ignore[attr-defined]
        assert (
            conn.execute("SELECT count(*) FROM bid_received_file_disposition_events").fetchone()[0]
            == count_before
        )

    duplicate = BulkFileDispositionCreate.model_construct(
        release_id=release_id,
        items=(
            BulkFileReviewItem(
                file_id=str(before[1]["file_id"]),
                supersedes_event_id=str(before[1]["disposition_event_id"]),
            ),
            BulkFileReviewItem(
                file_id=str(before[1]["file_id"]),
                supersedes_event_id=str(before[1]["disposition_event_id"]),
            ),
        ),
        analysis_eligibility=AnalysisEligibility.ELIGIBLE,
        content_form=None,
        exclusion_reason=None,
        operation_id="duplicate-selection",
    )
    with pytest.raises(IntakeNotFoundError, match="received release not found"):
        service.bulk_classify_files(
            BID_ID,
            _bulk_command("not-this-release", before[1:2], operation_id="wrong-release"),
            "Jason",
        )
    with pytest.raises(ValueError, match="only be selected once"):
        service.bulk_classify_files(BID_ID, duplicate, "Jason")


def test_bulk_review_rolls_back_every_event_when_audit_write_fails(tmp_path: Path) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    files = repository.release_detail(BID_ID, release_id)["files"]
    with database._conn() as conn:  # type: ignore[attr-defined]
        events_before = conn.execute(
            "SELECT count(*) FROM bid_received_file_disposition_events"
        ).fetchone()[0]
        audits_before = conn.execute("SELECT count(*) FROM audit_log").fetchone()[0]
    original_audit = repository._audit

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.IntegrityError("injected audit failure")

    repository._audit = fail_audit  # type: ignore[method-assign,assignment]
    with pytest.raises(sqlite3.IntegrityError, match="injected audit failure"):
        service.bulk_classify_files(BID_ID, _bulk_command(release_id, files[:2]), "Jason")
    repository._audit = original_audit  # type: ignore[method-assign,assignment]
    with database._conn() as conn:  # type: ignore[attr-defined]
        assert (
            conn.execute("SELECT count(*) FROM bid_received_file_disposition_events").fetchone()[0]
            == events_before
        )
        assert conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] == audits_before


def test_bulk_review_rejects_a_file_from_another_bid_without_mutation(tmp_path: Path) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    existing_bid = service.bid_repository.get_bid(BID_ID)
    assert existing_bid is not None
    other_bid_id = "B-2026-0012"
    service.bid_repository.create_bid(existing_bid.model_copy(update={"bid_id": other_bid_id}))
    preview = service.preview(other_bid_id, "landing")
    other_release = service.register_release(
        _command(preview.source_fingerprint, "bulk-other-bid").model_copy(
            update={"bid_id": other_bid_id}
        ),
        "Jason",
    )
    other_file = repository.release_detail(other_bid_id, str(other_release["release_id"]))["files"][
        0
    ]
    with database._conn() as conn:  # type: ignore[attr-defined]
        before = conn.execute(
            "SELECT count(*) FROM bid_received_file_disposition_events"
        ).fetchone()[0]
    with pytest.raises(IntakeNotFoundError, match="selected received file not found"):
        service.bulk_classify_files(BID_ID, _bulk_command(release_id, [other_file]), "Jason")
    with database._conn() as conn:  # type: ignore[attr-defined]
        assert (
            conn.execute("SELECT count(*) FROM bid_received_file_disposition_events").fetchone()[0]
            == before
        )


def test_bulk_review_rejects_an_empty_selection_before_mutation(tmp_path: Path) -> None:
    service, repository, database, release_id = _bulk_release(tmp_path)
    with database._conn() as conn:  # type: ignore[attr-defined]
        before = conn.execute(
            "SELECT count(*) FROM bid_received_file_disposition_events"
        ).fetchone()[0]
    with pytest.raises(ValidationError, match="at least 1 item"):
        service.bulk_classify_files(
            BID_ID,
            {
                "release_id": release_id,
                "items": [],
                "analysis_eligibility": "ELIGIBLE",
                "operation_id": "empty-selection",
            },
            "Jason",
        )
    with database._conn() as conn:  # type: ignore[attr-defined]
        assert (
            conn.execute("SELECT count(*) FROM bid_received_file_disposition_events").fetchone()[0]
            == before
        )


def test_migration_is_exact_additive_idempotent_and_immutable(tmp_path: Path) -> None:
    _service_instance, repository, database, _source, _managed = _service(tmp_path)
    assert len(repository.migration_tables()) == 18
    assert repository.migration_tables()[-1] == "bid_package_intake_schema_migrations"
    repository.__class__(database)
    with database._conn() as conn:
        evidence = conn.execute(
            "SELECT migration_id FROM bid_package_intake_schema_migrations"
        ).fetchall()
        assert [row["migration_id"] for row in evidence] == [OPS_11_MIGRATION_ID]
        trigger_count = conn.execute(
            """SELECT count(*) FROM sqlite_master WHERE type='trigger'
            AND (name LIKE 'bid_%_immutable' OR name LIKE 'bid_%_no_delete')"""
        ).fetchone()[0]
        assert trigger_count == 36
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE bid_package_intake_schema_migrations SET applied_at='changed'")
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute("DELETE FROM bid_package_intake_schema_migrations")
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_notice_can_precede_files_and_link_resolves_missing_release(tmp_path: Path) -> None:
    service, repository, _database, source, _managed = _service(tmp_path)
    notice = service.record_notice(
        ReleaseNoticeCreate(
            bid_id=BID_ID,
            release_type=ReleaseType.ADDENDUM,
            exact_customer_reference="ADD-2",
            customer_issue_date=date(2026, 9, 11),
            expected_receipt_date=date(2026, 9, 12),
            channel=ReleaseChannel.EMAIL,
            observed_at=NOW,
            expectation=NoticeExpectation.EXPECTED,
            summary="Customer says Addendum 2 was issued.",
            evidence_reference="Mailbox reference SYN-2",
            operation_id="notice-2",
        ),
        "Jason",
    )
    assert "EXPECTED_RELEASE_MISSING" in {item.code for item in repository.issue_blockers(BID_ID)}
    (source / "addendum.pdf").write_bytes(b"%PDF synthetic addendum")
    preview = service.preview(BID_ID, "landing")
    release = service.register_release(
        _command(preview.source_fingerprint, "add-2").model_copy(
            update={
                "release_type": ReleaseType.ADDENDUM,
                "exact_customer_reference": "ADD-2",
            }
        ),
        "Jason",
    )
    service.link_notice(
        BID_ID,
        str(notice["notice_id"]),
        str(release["release_id"]),
        "FULFILS",
        "notice-link-2",
        "Jason",
    )
    assert "EXPECTED_RELEASE_MISSING" not in {
        item.code for item in repository.issue_blockers(BID_ID)
    }


def test_directive_waiver_acknowledgement_and_snapshot_rules(tmp_path: Path) -> None:
    service, repository, database, source, _managed = _service(tmp_path)
    content = b"%PDF synthetic controlled source"
    (source / "controlled.pdf").write_bytes(content)
    _document_id, version_id = _controlled_version(database, content)
    preview = service.preview(BID_ID, "landing")
    release = service.register_release(
        _command(preview.source_fingerprint).model_copy(
            update={"exact_customer_reference": "   =DANGEROUS()"}
        ),
        "Jason",
    )
    release_id = str(release["release_id"])
    _review_and_link(service, repository, release_id, version_id)
    directive = service.record_directive(
        BID_ID,
        release_id,
        DirectiveCreate(
            directive_type="CHANGE",
            description="Use the revised synthetic requirement.",
            materiality=DirectiveMateriality.MATERIAL,
            target_document_version_id=version_id,
            operation_id="directive-1",
        ),
        "Jason",
    )
    provenance = Provenance.from_human("Jason").model_dump_json()
    with database._conn() as conn:
        conn.execute(
            """INSERT INTO approvals(approval_id,bid_id,approval_type,required,obtained,
            authority,evidence_ref,decision,decided_at,provenance_json)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                "APP-OPS11",
                BID_ID,
                "executive",
                1,
                1,
                "Director",
                "SYN-APP",
                "Approved internal administration exception",
                NOW.isoformat(),
                provenance,
            ),
        )
    waiver = service.dispose_directive(
        BID_ID,
        str(directive["directive_id"]),
        DirectiveDispositionCreate(
            status=DirectiveDispositionStatus.INTERNAL_WAIVER,
            rationale="Internal administration waived only.",
            approval_id="APP-OPS11",
            operation_id="waiver-1",
        ),
        "Jason",
    )
    assert any(
        "internal waiver" in issue for issue in repository.incorporation_issues(BID_ID, release_id)
    )
    with pytest.raises(ReleaseNotIncorporableError, match="internal waiver"):
        service.publish_basis(
            BID_ID,
            SnapshotCreate(
                release_ids=(release_id,),
                label="Blocked basis",
                operation_id="basis-blocked",
            ),
            "Jason",
        )
    service.dispose_directive(
        BID_ID,
        str(directive["directive_id"]),
        DirectiveDispositionCreate(
            status=DirectiveDispositionStatus.INCORPORATED,
            rationale="Exact controlled version incorporated.",
            resulting_document_version_id=version_id,
            supersedes_disposition_id=str(waiver["disposition_id"]),
            operation_id="incorporated-1",
        ),
        "Jason",
    )
    required = service.record_acknowledgement(
        BID_ID,
        release_id,
        AcknowledgementCreate(
            event_type=AcknowledgementEventType.REQUIRED,
            due_at=NOW,
            operation_id="ack-required",
        ),
        "Jason",
    )
    snapshot = service.publish_basis(
        BID_ID,
        SnapshotCreate(
            release_ids=(release_id,),
            label="Controlled Bid Basis 1",
            operation_id="basis-1",
        ),
        "Jason",
    )
    assert b"'   =DANGEROUS()" in service.basis_register_csv(BID_ID)
    assert "ACKNOWLEDGEMENT_OUTSTANDING" in {
        item.code for item in repository.issue_blockers(BID_ID)
    }
    linked_work = service.create_linked_work_item(
        bid_id=BID_ID,
        title="Obtain addendum acknowledgement",
        operation_id="ack-work",
        actor="Jason",
        acknowledgement_event_id=str(required["acknowledgement_event_id"]),
    )
    replayed_work = service.create_linked_work_item(
        bid_id=BID_ID,
        title="Obtain addendum acknowledgement",
        operation_id="ack-work",
        actor="Jason",
        acknowledgement_event_id=str(required["acknowledgement_event_id"]),
    )
    assert replayed_work.work_item_id == linked_work.work_item_id
    my_day = MyDayService(
        service.work_item_service.repository,
        service.bid_repository,
        database,
    ).get_my_day(as_of=NOW.date())
    assert any(row["code"] == "ACKNOWLEDGEMENT_DUE" for row in my_day.intake_attention)
    service.record_acknowledgement(
        BID_ID,
        release_id,
        AcknowledgementCreate(
            event_type=AcknowledgementEventType.ACKNOWLEDGED,
            acknowledgement_reference="SYN-ACK-1",
            supersedes_event_id=str(required["acknowledgement_event_id"]),
            operation_id="ack-complete",
        ),
        "Jason",
    )
    assert "ACKNOWLEDGEMENT_OUTSTANDING" not in {
        item.code for item in repository.issue_blockers(BID_ID)
    }
    with database._conn() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE bid_basis_snapshots SET label='changed' WHERE snapshot_id=?",
                (snapshot["snapshot_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute(
                "DELETE FROM bid_basis_snapshots WHERE snapshot_id=?",
                (snapshot["snapshot_id"],),
            )
    with pytest.raises(StaleIntakeError, match="current Bid Basis changed"):
        service.publish_basis(
            BID_ID,
            SnapshotCreate(
                expected_current_snapshot_id=None,
                release_ids=(release_id,),
                label="Stale publication",
                operation_id="basis-stale",
            ),
            "Jason",
        )


def test_file_decision_corrections_and_exact_version_validation(tmp_path: Path) -> None:
    service, repository, database, source, _managed = _service(tmp_path)
    content = b"same bytes"
    (source / "one.txt").write_bytes(content)
    _doc, version_id = _controlled_version(database, content)
    preview = service.preview(BID_ID, "landing")
    release = service.register_release(_command(preview.source_fingerprint), "Jason")
    release_id = str(release["release_id"])
    detail = repository.release_detail(BID_ID, release_id)
    file_row = detail["files"][0]
    file_id = str(file_row["file_id"])
    first = service.classify_file(
        BID_ID,
        file_id,
        FileDispositionCreate(
            content_form=ContentForm.UNKNOWN,
            classification_method=ClassificationMethod.HUMAN_REVIEW,
            analysis_eligibility=AnalysisEligibility.EXCLUDED,
            exclusion_reason="Archive-like supporting copy",
            supersedes_event_id=str(file_row["disposition_event_id"]),
            operation_id="file-excluded",
        ),
        "Jason",
    )
    service.classify_file(
        BID_ID,
        file_id,
        FileDispositionCreate(
            content_form=ContentForm.TEXTUAL,
            classification_method=ClassificationMethod.HUMAN_REVIEW,
            analysis_eligibility=AnalysisEligibility.ELIGIBLE,
            supersedes_event_id=str(first["disposition_event_id"]),
            operation_id="file-corrected",
        ),
        "Jason",
    )
    service.link_file_document(
        BID_ID,
        file_id,
        FileDocumentLinkCreate(
            document_version_id=version_id,
            relationship=FileDocumentRelationship.EXACT_BYTES,
            operation_id="file-link",
        ),
        "Jason",
    )
    with pytest.raises(ValidationError, match="exclusion reason"):
        FileDispositionCreate(
            content_form=ContentForm.TEXTUAL,
            classification_method=ClassificationMethod.HUMAN_REVIEW,
            analysis_eligibility=AnalysisEligibility.EXCLUDED,
            operation_id="invalid",
        )
    with database._conn() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE bid_received_files SET original_filename='renamed' WHERE file_id=?",
                (file_id,),
            )


def test_received_addendum_blocks_ops09_and_basis_change_stales_supporting_hash(
    tmp_path: Path,
) -> None:
    proposal, bids = service_with_ready_bid(tmp_path)
    database = proposal.db
    work = WorkItemRepository(database)
    repository = BidPackageRepository(database)
    source = tmp_path / "intake-source"
    source.mkdir()
    intake = BidPackageService(
        repository,
        bids,
        BidPackageStorage(tmp_path / "intake-managed", {"landing": source}),
        WorkItemService(work, bids),
        now_factory=lambda: NOW,
    )
    export, replayed = proposal.export_package(OPS09_BID_ID, "Jason")
    assert not replayed
    with database._conn() as conn:
        document = conn.execute(
            """SELECT id,current_version_id,control_version FROM documents
            WHERE bid_id=? AND control_managed=1 LIMIT 1""",
            (OPS09_BID_ID,),
        ).fetchone()
    assert document is not None
    old_version_id = str(document["current_version_id"])

    (source / "initial.pdf").write_bytes(b"synthetic initial occurrence")
    preview = intake.preview(OPS09_BID_ID, "landing")
    first = intake.register_release(
        _command(preview.source_fingerprint, "proposal-basis-1").model_copy(
            update={"bid_id": OPS09_BID_ID, "exact_customer_reference": "RFP-SYN"}
        ),
        "Jason",
    )
    first_id = str(first["release_id"])
    _review_and_link(
        intake,
        repository,
        first_id,
        old_version_id,
        relationship=FileDocumentRelationship.REPRESENTS_VERSION,
        bid_id=OPS09_BID_ID,
    )
    first_snapshot = intake.publish_basis(
        OPS09_BID_ID,
        SnapshotCreate(
            release_ids=(first_id,),
            label="Initial Bid Basis",
            operation_id="proposal-snapshot-1",
        ),
        "Jason",
    )
    integrated = ProposalExchangeService(
        database,
        bids,
        tmp_path / "artifacts",
        now_factory=lambda: NOW,
        intake_blocker_loader=repository.issue_blockers,
    )
    assert "supporting_documents" not in integrated.assess(OPS09_BID_ID).changed_areas

    new_content = b"%PDF synthetic revised controlled version"
    new_version_id = "DV-00000000-0000-0000-0000-000000009999"
    provenance = Provenance.from_human("Jason").model_dump_json()
    with database._conn() as conn:
        conn.create_function("contractiq_version_transition_allowed", 0, lambda: 1)
        conn.create_function("contractiq_controlled_update_allowed", 0, lambda: 1)
        conn.execute(
            "UPDATE document_versions SET version_state='SUPERSEDED' WHERE document_version_id=?",
            (old_version_id,),
        )
        conn.execute(
            """INSERT INTO document_versions(document_version_id,document_id,version_label,
            issued_date,received_at,original_filename,media_type,byte_size,sha256_digest,
            storage_key,predecessor_version_id,version_state,created_at,provenance_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                new_version_id,
                document["id"],
                "B",
                "2026-09-11",
                NOW.isoformat(),
                "revised.pdf",
                "application/pdf",
                len(new_content),
                hashlib.sha256(new_content).hexdigest(),
                "versions/99/revised.bin",
                old_version_id,
                "CURRENT",
                NOW.isoformat(),
                provenance,
            ),
        )
        conn.execute(
            """UPDATE documents SET current_version_id=?,control_version=control_version+1,
            control_updated_at=? WHERE id=?""",
            (new_version_id, NOW.isoformat(), document["id"]),
        )
    (source / "initial.pdf").unlink()
    (source / "addendum.pdf").write_bytes(new_content)
    addendum_preview = intake.preview(OPS09_BID_ID, "landing")
    second = intake.register_release(
        _command(addendum_preview.source_fingerprint, "proposal-basis-2").model_copy(
            update={
                "bid_id": OPS09_BID_ID,
                "release_type": ReleaseType.ADDENDUM,
                "exact_customer_reference": "ADD-SYN-1",
            }
        ),
        "Jason",
    )
    second_id = str(second["release_id"])
    assert "RECEIVED_RELEASE_NOT_IN_BASIS" in {
        blocker.code for blocker in integrated.assess(OPS09_BID_ID).blockers
    }
    _review_and_link(
        intake,
        repository,
        second_id,
        new_version_id,
        bid_id=OPS09_BID_ID,
    )
    intake.publish_basis(
        OPS09_BID_ID,
        SnapshotCreate(
            expected_current_snapshot_id=str(first_snapshot["snapshot_id"]),
            release_ids=(first_id, second_id),
            label="Addendum Bid Basis",
            operation_id="proposal-snapshot-2",
        ),
        "Jason",
    )
    assessment = integrated.assess(OPS09_BID_ID)
    assert "supporting_documents" in assessment.changed_areas
    assert all(blocker.code != "RECEIVED_RELEASE_NOT_IN_BASIS" for blocker in assessment.blockers)
