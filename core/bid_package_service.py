"""Application boundary for controlled Bid package intake and Bid Basis publication."""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import BinaryIO, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from core.bid_package_intake import (
    AcknowledgementCreate,
    BulkFileDispositionCreate,
    ChannelCheckCreate,
    DirectiveCreate,
    DirectiveDispositionCreate,
    FileDispositionCreate,
    FileDocumentLinkCreate,
    IntakeInbox,
    PackageIntakeSummary,
    ReleaseNoticeCreate,
    ReleasePreview,
    ReleaseRegistration,
    SnapshotCreate,
)
from core.bid_package_repository import BidPackageRepository, IntakeConflictError
from core.bid_package_storage import (
    BidPackageStorage,
    IntakePublicationError,
    SourceChangedError,
)
from core.bid_repository import BidRepository
from core.enums import Actor
from core.export_controls import csv_safe_row
from core.schemas import Provenance
from core.work_item_service import WorkItemService
from core.work_items import (
    ResponsibilityDomain,
    WorkCategory,
    WorkItem,
    WorkItemCreate,
    WorkItemPriority,
)


class BidPackageService:
    """Validate business commands and coordinate DB/filesystem transactions."""

    def __init__(
        self,
        repository: BidPackageRepository,
        bid_repository: BidRepository,
        storage: BidPackageStorage,
        work_item_service: WorkItemService,
        *,
        now_factory: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self.repository = repository
        self.bid_repository = bid_repository
        self.storage = storage
        self.work_item_service = work_item_service
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4

    def _now(self) -> datetime:
        value = self._now_factory()
        if value.tzinfo is None:
            raise ValueError("now_factory must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}-{self._id_factory()}"

    @staticmethod
    def _stable_id(prefix: str, operation_id: str, purpose: str) -> str:
        return f"{prefix}-{uuid5(NAMESPACE_URL, f'contractiq-ops11:{operation_id}:{purpose}')}"

    @staticmethod
    def _actor(actor: str) -> str:
        normalized = actor.strip()
        if not normalized:
            raise ValueError("actor must be non-empty")
        return normalized

    @staticmethod
    def _provenance(actor: str, at: datetime) -> Provenance:
        return Provenance(
            created_by=Actor.HUMAN,
            agent_name=actor,
            created_at=at,
            human_confirmed=True,
            confirmed_by=actor,
            confirmed_at=at,
        )

    def _require_bid(self, bid_id: str) -> None:
        if self.bid_repository.get_bid(bid_id) is None:
            raise ValueError(f"Bid not found: {bid_id}")

    def preview(
        self, bid_id: str, source_root_alias: str, source_folder: str | None = None
    ) -> ReleasePreview:
        """Produce a mutation-free file inventory for an allowlisted source root."""
        self._require_bid(bid_id)
        return self.storage.preview(source_root_alias, source_folder)

    def intake_inboxes(self, bid_id: str) -> tuple[IntakeInbox, ...]:
        """Project safe configured inbox folders and their prior-import status."""
        self._require_bid(bid_id)
        imported_fingerprints = {
            str(release["source_fingerprint"])
            for release in self.repository.summary(bid_id).releases
        }
        inboxes: list[IntakeInbox] = []
        for alias, _label in self.storage.source_choices():
            inbox = self.storage.inbox(alias)
            folders = tuple(
                folder.model_copy(
                    update={
                        "already_imported": folder.source_fingerprint in imported_fingerprints
                        if folder.source_fingerprint
                        else False
                    }
                )
                for folder in inbox.folders
            )
            inboxes.append(inbox.model_copy(update={"folders": folders}))
        return tuple(inboxes)

    def register_release(
        self, data: ReleaseRegistration | Mapping[str, object], actor: str
    ) -> dict[str, object]:
        """Confirm one preview through a compensated DB/filesystem publication."""
        command = ReleaseRegistration.model_validate(data)
        normalized_actor = self._actor(actor)
        self._require_bid(command.bid_id)
        existing = self.repository.get_release_by_operation(command.bid_id, command.operation_id)
        if existing is not None:
            return existing
        preview = self.storage.preview(command.source_root_alias, command.source_folder)
        if preview.source_fingerprint != command.expected_source_fingerprint:
            raise SourceChangedError("source changed after preview; preview the release again")
        release_id = self._stable_id("REL", command.operation_id, "release")
        file_ids = {
            item.original_relative_path: self._stable_id(
                "RF", command.operation_id, item.original_relative_path
            )
            for item in preview.files
        }
        staged = self.storage.stage(
            preview,
            release_id,
            command.operation_id,
            file_ids,
        )
        at = self._now()
        provenance = self._provenance(normalized_actor, at)
        try:
            release, created = self.repository.register_release(
                release_id=release_id,
                processing_run_id=self._stable_id("RUN", command.operation_id, "registration"),
                result_ids=[
                    self._stable_id("PFR", command.operation_id, item.original_relative_path)
                    for item in preview.files
                ],
                default_disposition_ids=[
                    self._stable_id("FDE", command.operation_id, item.original_relative_path)
                    for item in preview.files
                ],
                command=command,
                preview=preview,
                staged=staged,
                file_ids=file_ids,
                actor=normalized_actor,
                at=at,
                provenance=provenance,
                audit_id=self._stable_id("AUD", command.operation_id, "release-registration"),
                publish=self.storage.publish,
                compensate=self.storage.remove_published,
            )
            if not created:
                self.storage.discard_staged(staged)
            return release
        except Exception:
            if staged.stage_directory.exists():
                self.storage.quarantine(staged.stage_directory, command.operation_id)
            raise

    def record_notice(
        self, data: ReleaseNoticeCreate | Mapping[str, object], actor: str
    ) -> dict[str, object]:
        command = ReleaseNoticeCreate.model_validate(data)
        normalized_actor = self._actor(actor)
        self._require_bid(command.bid_id)
        at = self._now()
        return self.repository.create_notice(
            self._new_id("RN"),
            command,
            actor=normalized_actor,
            at=at,
            provenance=self._provenance(normalized_actor, at),
            audit_id=self._new_id("AUD"),
        )

    def record_channel_check(
        self, data: ChannelCheckCreate | Mapping[str, object], actor: str
    ) -> dict[str, object]:
        command = ChannelCheckCreate.model_validate(data)
        normalized_actor = self._actor(actor)
        self._require_bid(command.bid_id)
        at = self._now()
        return self.repository.create_channel_check(
            self._new_id("RCC"),
            command,
            actor=normalized_actor,
            at=at,
            provenance=self._provenance(normalized_actor, at),
            audit_id=self._new_id("AUD"),
        )

    def link_notice(
        self,
        bid_id: str,
        notice_id: str,
        release_id: str,
        relationship: str,
        operation_id: str,
        actor: str,
    ) -> dict[str, object]:
        normalized_actor = self._actor(actor)
        at = self._now()
        return self.repository.link_notice(
            link_id=self._new_id("RNL"),
            bid_id=bid_id,
            notice_id=notice_id,
            release_id=release_id,
            relationship=relationship,
            operation_id=operation_id,
            actor=normalized_actor,
            at=at,
            provenance=self._provenance(normalized_actor, at),
            audit_id=self._new_id("AUD"),
        )

    def classify_file(
        self,
        bid_id: str,
        file_id: str,
        data: FileDispositionCreate | Mapping[str, object],
        actor: str,
    ) -> dict[str, object]:
        command = FileDispositionCreate.model_validate(data)
        normalized_actor = self._actor(actor)
        at = self._now()
        return self.repository.add_file_disposition(
            event_id=self._new_id("FDE"),
            bid_id=bid_id,
            file_id=file_id,
            command=command,
            actor=normalized_actor,
            at=at,
            provenance=self._provenance(normalized_actor, at),
            audit_id=self._new_id("AUD"),
        )

    def bulk_classify_files(
        self,
        bid_id: str,
        data: BulkFileDispositionCreate | Mapping[str, object],
        actor: str,
    ) -> tuple[dict[str, object], ...]:
        """Apply one human review decision to selected files from one received package.

        The repository validates every selected file and writes every resulting immutable event
        and audit row in one transaction.  A no-op selection is still concurrency-checked but
        creates no new event.
        """
        command = BulkFileDispositionCreate.model_validate(data)
        normalized_actor = self._actor(actor)
        self._require_bid(bid_id)
        at = self._now()
        return self.repository.add_file_dispositions_bulk(
            bid_id=bid_id,
            command=command,
            event_ids=tuple(self._new_id("FDE") for _ in command.items),
            audit_ids=tuple(self._new_id("AUD") for _ in command.items),
            actor=normalized_actor,
            at=at,
            provenance=self._provenance(normalized_actor, at),
        )

    def link_file_document(
        self,
        bid_id: str,
        file_id: str,
        data: FileDocumentLinkCreate | Mapping[str, object],
        actor: str,
    ) -> dict[str, object]:
        command = FileDocumentLinkCreate.model_validate(data)
        normalized_actor = self._actor(actor)
        at = self._now()
        return self.repository.link_file_document(
            link_id=self._new_id("FDL"),
            bid_id=bid_id,
            file_id=file_id,
            command=command,
            actor=normalized_actor,
            at=at,
            provenance=self._provenance(normalized_actor, at),
            audit_id=self._new_id("AUD"),
        )

    def record_directive(
        self,
        bid_id: str,
        release_id: str,
        data: DirectiveCreate | Mapping[str, object],
        actor: str,
    ) -> dict[str, object]:
        command = DirectiveCreate.model_validate(data)
        normalized_actor = self._actor(actor)
        at = self._now()
        return self.repository.add_directive(
            directive_id=self._new_id("DIR"),
            bid_id=bid_id,
            release_id=release_id,
            command=command,
            actor=normalized_actor,
            at=at,
            provenance=self._provenance(normalized_actor, at),
            audit_id=self._new_id("AUD"),
        )

    def dispose_directive(
        self,
        bid_id: str,
        directive_id: str,
        data: DirectiveDispositionCreate | Mapping[str, object],
        actor: str,
    ) -> dict[str, object]:
        command = DirectiveDispositionCreate.model_validate(data)
        normalized_actor = self._actor(actor)
        at = self._now()
        return self.repository.add_directive_disposition(
            disposition_id=self._new_id("DD"),
            bid_id=bid_id,
            directive_id=directive_id,
            command=command,
            actor=normalized_actor,
            at=at,
            provenance=self._provenance(normalized_actor, at),
            audit_id=self._new_id("AUD"),
        )

    def record_acknowledgement(
        self,
        bid_id: str,
        release_id: str,
        data: AcknowledgementCreate | Mapping[str, object],
        actor: str,
    ) -> dict[str, object]:
        command = AcknowledgementCreate.model_validate(data)
        normalized_actor = self._actor(actor)
        at = self._now()
        return self.repository.add_acknowledgement(
            event_id=self._new_id("ACK"),
            bid_id=bid_id,
            release_id=release_id,
            command=command,
            actor=normalized_actor,
            at=at,
            provenance=self._provenance(normalized_actor, at),
            audit_id=self._new_id("AUD"),
        )

    def publish_basis(
        self,
        bid_id: str,
        data: SnapshotCreate | Mapping[str, object],
        actor: str,
    ) -> dict[str, object]:
        command = SnapshotCreate.model_validate(data)
        normalized_actor = self._actor(actor)
        self._require_bid(bid_id)
        at = self._now()
        document_count = self.repository.candidate_snapshot_document_count(
            bid_id, command.release_ids
        )
        return self.repository.create_snapshot(
            snapshot_id=self._new_id("BBS"),
            snapshot_release_ids=[self._new_id("BBSR") for _ in command.release_ids],
            snapshot_document_ids=[self._new_id("BBSD") for _ in range(document_count)],
            bid_id=bid_id,
            command=command,
            actor=normalized_actor,
            at=at,
            provenance=self._provenance(normalized_actor, at),
            audit_id=self._new_id("AUD"),
        )

    def summary(self, bid_id: str) -> PackageIntakeSummary:
        self._require_bid(bid_id)
        return self.repository.summary(bid_id)

    def release_detail(self, bid_id: str, release_id: str) -> dict[str, object]:
        return self.repository.release_detail(bid_id, release_id)

    def basis_register(self, bid_id: str, snapshot_id: str | None = None) -> dict[str, object]:
        return self.repository.basis_register(bid_id, snapshot_id)

    def basis_register_csv(self, bid_id: str, snapshot_id: str | None = None) -> bytes:
        register = self.basis_register(bid_id, snapshot_id)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        bid = cast(dict[str, object], register["bid"])
        snapshot = cast(dict[str, object], register["snapshot"])
        writer.writerow(csv_safe_row(["Bid Basis Register", bid["bid_id"], bid["project_name"]]))
        writer.writerow(csv_safe_row(["Snapshot", snapshot["snapshot_id"], snapshot["created_at"]]))
        writer.writerow([])
        writer.writerow(["Release reference", "Type", "Customer issue date", "Received", "Channel"])
        for release in cast(list[dict[str, object]], register["releases"]):
            writer.writerow(
                csv_safe_row(
                    [
                        release["exact_customer_reference"] or "Not supplied",
                        release["release_type"],
                        release["customer_issue_date"] or "",
                        release["received_at"],
                        release["received_channel"],
                    ]
                )
            )
        writer.writerow([])
        writer.writerow(["Document number", "Title", "Revision", "Exact version", "Role"])
        for document in cast(list[dict[str, object]], register["documents"]):
            writer.writerow(
                csv_safe_row(
                    [
                        document["document_number"] or "",
                        document["control_title"],
                        document["version_label"],
                        document["document_version_id"],
                        document["basis_role"],
                    ]
                )
            )
        writer.writerow([])
        writer.writerow(["Directive", "Materiality", "Disposition", "Rationale"])
        for directive in cast(list[dict[str, object]], register["directives"]):
            writer.writerow(
                csv_safe_row(
                    [
                        directive["description"],
                        directive["materiality"],
                        directive["status"],
                        directive["rationale"],
                    ]
                )
            )
        writer.writerow([])
        writer.writerow(["Explicit exclusion / qualification", "Customer basis", "Bid response"])
        for item in cast(list[dict[str, object]], register["qualifications"]):
            writer.writerow(
                csv_safe_row(
                    [
                        str(item["disposition"]).replace("_", " ").title(),
                        item["statement"],
                        item["response_text"] or "",
                    ]
                )
            )
        return output.getvalue().encode("utf-8-sig")

    def open_received_file(
        self, bid_id: str, release_id: str, file_id: str
    ) -> tuple[BinaryIO, dict[str, object]]:
        evidence = self.repository.file_download_evidence(bid_id, release_id, file_id)
        source = self.storage.open_read(str(evidence["managed_storage_key"]))
        digest = hashlib.sha256()
        size = 0
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
        if size != int(str(evidence["byte_size"])) or digest.hexdigest() != evidence["sha256"]:
            source.close()
            raise IntakeConflictError("managed received file failed integrity verification")
        source.seek(0)
        return source, evidence

    def reverify_release(
        self, bid_id: str, release_id: str, operation_id: str, actor: str
    ) -> dict[str, object]:
        """Idempotently re-check every managed original without changing evidence."""
        detail = self.repository.release_detail(bid_id, release_id)
        results: list[tuple[str, str, str]] = []
        for item in cast(list[dict[str, object]], detail["files"]):
            file_id = str(item["file_id"])
            try:
                source, _evidence = self.open_received_file(bid_id, release_id, file_id)
                source.close()
                results.append((file_id, "SUCCEEDED", "Managed bytes match immutable evidence."))
            except (OSError, IntakeConflictError, IntakePublicationError) as exc:
                results.append((file_id, "FAILED", str(exc)))
        normalized_actor = self._actor(actor)
        at = self._now()
        return self.repository.record_reprocess(
            processing_run_id=self._stable_id("RUN", operation_id, "reverify"),
            result_ids=[self._stable_id("PFR", operation_id, file_id) for file_id, _, _ in results],
            bid_id=bid_id,
            release_id=release_id,
            operation_id=operation_id,
            results=results,
            actor=normalized_actor,
            at=at,
            provenance=self._provenance(normalized_actor, at),
            audit_id=self._stable_id("AUD", operation_id, "reverify"),
        )

    def create_linked_work_item(
        self,
        *,
        bid_id: str,
        title: str,
        operation_id: str,
        actor: str,
        notice_id: str | None = None,
        release_id: str | None = None,
        file_id: str | None = None,
        directive_id: str | None = None,
        acknowledgement_event_id: str | None = None,
    ) -> WorkItem:
        existing = self.repository.work_link_by_operation(operation_id)
        if existing is not None:
            return self.work_item_service.get_work_item(str(existing["work_item_id"]))
        self.repository.validate_work_target(
            bid_id=bid_id,
            notice_id=notice_id,
            release_id=release_id,
            file_id=file_id,
            directive_id=directive_id,
            acknowledgement_event_id=acknowledgement_event_id,
        )
        item = self.work_item_service.create_work_item(
            WorkItemCreate(
                bid_id=bid_id,
                title=title,
                priority=WorkItemPriority.HIGH,
                category=WorkCategory.CUSTOMER_REQUEST,
                responsibility_domain=ResponsibilityDomain.EPC_PROJECT_PURSUIT,
            ),
            actor,
        )
        normalized_actor = self._actor(actor)
        at = self._now()
        self.repository.link_work_item(
            link_id=self._new_id("IWL"),
            bid_id=bid_id,
            work_item_id=item.work_item_id,
            operation_id=operation_id,
            actor=normalized_actor,
            at=at,
            provenance=self._provenance(normalized_actor, at),
            audit_id=self._new_id("AUD"),
            notice_id=notice_id,
            release_id=release_id,
            file_id=file_id,
            directive_id=directive_id,
            acknowledgement_event_id=acknowledgement_event_id,
        )
        return item


__all__ = ["BidPackageService"]
