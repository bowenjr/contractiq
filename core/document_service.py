"""Application service coordinating controlled metadata, files, and audit."""

import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import BinaryIO, cast
from uuid import UUID, uuid4, uuid5

from core.bid_repository import BidRepository
from core.document_control import (
    ControlledDocument,
    DocumentCategory,
    DocumentCreate,
    DocumentLifecycle,
    DocumentMetadataEdit,
    DocumentRegisterEntry,
    DocumentVersion,
    DocumentVersionCreate,
    DocumentVersionState,
    IntegrityResult,
    IntegrityStatus,
    StorageDiagnostic,
)
from core.document_repository import (
    ControlledDocumentNotFoundError,
    DocumentRepository,
    DocumentVersionNotFoundError,
    DuplicateDocumentVersionError,
    StaleDocumentError,
)
from core.enums import Actor
from core.managed_document_storage import (
    ManagedDocumentStorage,
    StorageCollisionError,
    normalize_display_filename,
)
from core.schemas import AuditEntry, Provenance

DocumentCreateData = DocumentCreate | Mapping[str, object]
DocumentEditData = DocumentMetadataEdit | Mapping[str, object]
DocumentVersionCreateData = DocumentVersionCreate | Mapping[str, object]


class DocumentService:
    """The only supported mutation boundary for controlled documents."""

    def __init__(
        self,
        repository: DocumentRepository,
        bid_repository: BidRepository,
        storage: ManagedDocumentStorage,
        *,
        now_factory: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self.repository = repository
        self.bid_repository = bid_repository
        self.storage = storage
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4

    def _now(self) -> datetime:
        value = self._now_factory()
        if value.tzinfo is None:
            raise ValueError("now_factory must return a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _actor(actor: str) -> str:
        normalized = actor.strip()
        if not normalized:
            raise ValueError("actor must be non-empty")
        return normalized

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}-{self._id_factory()}"

    @staticmethod
    def _provenance(actor: str, at: datetime, document_id: str) -> Provenance:
        return Provenance(
            created_by=Actor.HUMAN,
            agent_name=actor,
            source_document_id=document_id,
            created_at=at,
            human_confirmed=True,
            confirmed_by=actor,
            confirmed_at=at,
        )

    def _audit(
        self,
        *,
        document: ControlledDocument,
        actor: str,
        action: str,
        at: datetime,
        evidence: Mapping[str, object],
    ) -> AuditEntry:
        return AuditEntry(
            entry_id=self._new_id("AUD"),
            bid_id=document.bid_id,
            actor=actor,
            action=action,
            detail=json.dumps(
                {
                    "document_id": document.document_id,
                    "operation": action,
                    "evidence": dict(evidence),
                },
                sort_keys=True,
                default=str,
            ),
            timestamp=at,
        )

    def register_document(
        self,
        data: DocumentCreateData,
        source: BinaryIO,
        original_filename: str,
        media_type: str | None,
        actor: str,
    ) -> tuple[ControlledDocument, DocumentVersion]:
        """Ingest first bytes, then atomically create document/version/audit metadata."""
        request = DocumentCreate.model_validate(data)
        normalized_actor = self._actor(actor)
        if self.bid_repository.get_bid(request.bid_id) is None:
            raise ValueError(f"Bid not found: {request.bid_id}")
        display_filename = normalize_display_filename(original_filename)
        staged = self.storage.stage(source)
        placed_key: str | None = None
        try:
            at = self._now()
            document_id = self._new_id("DOC")
            version_id = self._new_id("DV")
            storage_key = self.storage.storage_key(version_id)
            provenance = self._provenance(normalized_actor, at, document_id)
            document = ControlledDocument(
                document_id=document_id,
                bid_id=request.bid_id,
                title=request.title,
                document_number=request.document_number,
                category=request.category,
                issuer=request.issuer,
                notes=request.notes,
                lifecycle_state=DocumentLifecycle.ACTIVE,
                current_version_id=version_id,
                created_at=at,
                updated_at=at,
                version=1,
                provenance=provenance,
            )
            version = DocumentVersion(
                document_version_id=version_id,
                document_id=document_id,
                version_label=request.version_label,
                issued_date=request.issued_date,
                received_at=request.received_at,
                original_filename=display_filename,
                media_type=media_type.strip() if media_type and media_type.strip() else None,
                byte_size=staged.byte_size,
                sha256_digest=staged.sha256_digest,
                storage_key=storage_key,
                predecessor_version_id=None,
                version_state=DocumentVersionState.CURRENT,
                created_at=at,
                provenance=provenance,
            )
            self.storage.place(staged, storage_key)
            placed_key = storage_key
            self.repository.create_with_first_version(
                document,
                version,
                self._audit(
                    document=document,
                    actor=normalized_actor,
                    action="controlled_document_created",
                    at=at,
                    evidence={
                        "document_version_id": version_id,
                        "version_label": version.version_label,
                        "original_filename": display_filename,
                        "byte_size": version.byte_size,
                        "sha256_digest": version.sha256_digest,
                    },
                ),
            )
            return document, version
        except Exception:
            if placed_key is not None:
                self.storage.remove_owned(placed_key)
            raise
        finally:
            self.storage.remove_staged(staged)

    def register_document_idempotent(
        self,
        operation_id: str,
        data: DocumentCreateData,
        source: BinaryIO,
        original_filename: str,
        media_type: str | None,
        actor: str,
    ) -> tuple[ControlledDocument, DocumentVersion]:
        """Register once using a server-issued operation identity stored in existing PKs."""
        try:
            operation_uuid = UUID(operation_id)
        except ValueError as exc:
            raise ValueError("source operation identity is invalid") from exc
        request = DocumentCreate.model_validate(data)
        normalized_actor = self._actor(actor)
        if self.bid_repository.get_bid(request.bid_id) is None:
            raise ValueError(f"Bid not found: {request.bid_id}")
        display_filename = normalize_display_filename(original_filename)
        normalized_media_type = media_type.strip() if media_type and media_type.strip() else None
        document_id = f"DOC-{uuid5(operation_uuid, 'controlled-document')}"
        version_id = f"DV-{uuid5(operation_uuid, 'controlled-document-version')}"
        audit_id = f"AUD-{uuid5(operation_uuid, 'controlled-document-audit')}"
        staged = self.storage.stage(source)
        storage_key = self.storage.storage_key(version_id)

        def recover() -> tuple[ControlledDocument, DocumentVersion] | None:
            document = self.repository.get(document_id)
            if document is None:
                return None
            version = self.repository.get_version(version_id)
            if version is None:
                raise ValueError("source operation has incomplete persisted evidence")
            expected = (
                request.bid_id,
                request.title,
                request.document_number,
                request.category,
                request.issuer,
                request.notes,
                version_id,
                request.version_label,
                request.issued_date,
                request.received_at,
                display_filename,
                normalized_media_type,
                staged.byte_size,
                staged.sha256_digest,
            )
            actual = (
                document.bid_id,
                document.title,
                document.document_number,
                document.category,
                document.issuer,
                document.notes,
                document.current_version_id,
                version.version_label,
                version.issued_date,
                version.received_at,
                version.original_filename,
                version.media_type,
                version.byte_size,
                version.sha256_digest,
            )
            if actual != expected:
                raise ValueError("source operation was already used with different values")
            if self.storage.verify(version).status is not IntegrityStatus.OK:
                raise ValueError("source operation evidence is not intact")
            return document, version

        placed_key: str | None = None
        try:
            existing = recover()
            if existing is not None:
                return existing
            at = self._now()
            provenance = self._provenance(normalized_actor, at, document_id)
            document = ControlledDocument(
                document_id=document_id,
                bid_id=request.bid_id,
                title=request.title,
                document_number=request.document_number,
                category=request.category,
                issuer=request.issuer,
                notes=request.notes,
                lifecycle_state=DocumentLifecycle.ACTIVE,
                current_version_id=version_id,
                created_at=at,
                updated_at=at,
                version=1,
                provenance=provenance,
            )
            version = DocumentVersion(
                document_version_id=version_id,
                document_id=document_id,
                version_label=request.version_label,
                issued_date=request.issued_date,
                received_at=request.received_at,
                original_filename=display_filename,
                media_type=normalized_media_type,
                byte_size=staged.byte_size,
                sha256_digest=staged.sha256_digest,
                storage_key=storage_key,
                predecessor_version_id=None,
                version_state=DocumentVersionState.CURRENT,
                created_at=at,
                provenance=provenance,
            )
            try:
                self.storage.place(staged, storage_key)
                placed_key = storage_key
            except StorageCollisionError:
                for _attempt in range(100):
                    existing = recover()
                    if existing is not None:
                        return existing
                    if not self.storage.resolve(storage_key).exists():
                        self.storage.place(staged, storage_key)
                        placed_key = storage_key
                        break
                    time.sleep(0.01)
                else:
                    raise ValueError("source operation is still being processed") from None
            try:
                self.repository.create_with_first_version(
                    document,
                    version,
                    AuditEntry(
                        entry_id=audit_id,
                        bid_id=document.bid_id,
                        actor=normalized_actor,
                        action="controlled_document_created",
                        detail=json.dumps(
                            {
                                "document_id": document_id,
                                "operation": "controlled_document_created",
                                "evidence": {
                                    "document_version_id": version_id,
                                    "version_label": version.version_label,
                                    "original_filename": display_filename,
                                    "byte_size": version.byte_size,
                                    "sha256_digest": version.sha256_digest,
                                },
                            },
                            sort_keys=True,
                        ),
                        timestamp=at,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = recover()
                if existing is not None:
                    placed_key = None
                    return existing
                raise
            return document, version
        except Exception:
            if placed_key is not None:
                self.storage.remove_owned(placed_key)
            raise
        finally:
            self.storage.remove_staged(staged)

    def get_document(self, document_id: str) -> ControlledDocument:
        """Fetch a controlled logical document or raise a stable error."""
        document = self.repository.get(document_id)
        if document is None:
            raise ControlledDocumentNotFoundError(f"Controlled document not found: {document_id}")
        return document

    def get_version(self, document_version_id: str) -> DocumentVersion:
        """Fetch immutable version evidence or raise a stable error."""
        version = self.repository.get_version(document_version_id)
        if version is None:
            raise DocumentVersionNotFoundError(f"Document version not found: {document_version_id}")
        return version

    def list_documents(
        self,
        *,
        bid_id: str | None = None,
        category: DocumentCategory | str | None = None,
        lifecycle: DocumentLifecycle | str | None = None,
    ) -> list[ControlledDocument]:
        """Delegate deterministic filtering to the repository."""
        typed_category = (
            category
            if isinstance(category, DocumentCategory)
            else DocumentCategory(category)
            if category is not None
            else None
        )
        typed_lifecycle = (
            lifecycle
            if isinstance(lifecycle, DocumentLifecycle)
            else DocumentLifecycle(lifecycle)
            if lifecycle is not None
            else None
        )
        return self.repository.list_documents(
            bid_id=bid_id,
            category=typed_category,
            lifecycle=typed_lifecycle,
        )

    def list_register_entries(
        self,
        *,
        bid_id: str | None = None,
        category: DocumentCategory | str | None = None,
        lifecycle: DocumentLifecycle | str | None = None,
    ) -> list[DocumentRegisterEntry]:
        """Return a register that isolates persisted integrity failures per row."""
        typed_category = (
            category
            if isinstance(category, DocumentCategory)
            else DocumentCategory(category)
            if category is not None
            else None
        )
        typed_lifecycle = (
            lifecycle
            if isinstance(lifecycle, DocumentLifecycle)
            else DocumentLifecycle(lifecycle)
            if lifecycle is not None
            else None
        )
        return self.repository.list_register_entries(
            bid_id=bid_id,
            category=typed_category,
            lifecycle=typed_lifecycle,
        )

    def list_versions(self, document_id: str) -> list[DocumentVersion]:
        """Return version history after confirming the parent exists."""
        self.get_document(document_id)
        return self.repository.list_versions(document_id)

    def add_version(
        self,
        document_id: str,
        data: DocumentVersionCreateData,
        source: BinaryIO,
        original_filename: str,
        media_type: str | None,
        actor: str,
    ) -> tuple[ControlledDocument, DocumentVersion]:
        """Stage a successor and atomically supersede the expected current version."""
        request = DocumentVersionCreate.model_validate(data)
        normalized_actor = self._actor(actor)
        current = self.get_document(document_id)
        if current.lifecycle_state == DocumentLifecycle.WITHDRAWN:
            raise ValueError("withdrawn documents cannot receive new versions")
        if (
            current.version != request.expected_document_version
            or current.current_version_id != request.expected_current_version_id
        ):
            raise StaleDocumentError("controlled document/current version changed")
        display_filename = normalize_display_filename(original_filename)
        staged = self.storage.stage(source)
        placed_key: str | None = None
        try:
            if self.repository.has_digest(document_id, staged.sha256_digest):
                raise DuplicateDocumentVersionError(
                    "identical bytes already exist for this controlled document"
                )
            at = self._now()
            version_id = self._new_id("DV")
            storage_key = self.storage.storage_key(version_id)
            provenance = self._provenance(normalized_actor, at, document_id)
            version = DocumentVersion(
                document_version_id=version_id,
                document_id=document_id,
                version_label=request.version_label,
                issued_date=request.issued_date,
                received_at=request.received_at,
                original_filename=display_filename,
                media_type=media_type.strip() if media_type and media_type.strip() else None,
                byte_size=staged.byte_size,
                sha256_digest=staged.sha256_digest,
                storage_key=storage_key,
                predecessor_version_id=current.current_version_id,
                version_state=DocumentVersionState.CURRENT,
                created_at=at,
                provenance=provenance,
            )
            updated = current.model_copy(
                update={
                    "current_version_id": version_id,
                    "updated_at": at,
                    "version": current.version + 1,
                }
            )
            self.storage.place(staged, storage_key)
            placed_key = storage_key
            self.repository.add_version(
                updated,
                version,
                request.expected_document_version,
                request.expected_current_version_id,
                self._audit(
                    document=updated,
                    actor=normalized_actor,
                    action="controlled_document_version_added",
                    at=at,
                    evidence={
                        "document_version_id": version_id,
                        "predecessor_version_id": version.predecessor_version_id,
                        "version_label": version.version_label,
                        "original_filename": display_filename,
                        "byte_size": version.byte_size,
                        "sha256_digest": version.sha256_digest,
                    },
                ),
            )
            return updated, version
        except Exception:
            if placed_key is not None:
                self.storage.remove_owned(placed_key)
            raise
        finally:
            self.storage.remove_staged(staged)

    def update_metadata(
        self,
        document_id: str,
        data: DocumentEditData,
        actor: str,
    ) -> ControlledDocument:
        """Apply only permitted logical metadata changes with optimistic concurrency."""
        request = DocumentMetadataEdit.model_validate(data)
        normalized_actor = self._actor(actor)
        current = self.get_document(document_id)
        if current.version != request.expected_version:
            raise StaleDocumentError("controlled document changed before metadata update")
        updates = request.model_dump(exclude_unset=True)
        updates.pop("expected_version", None)
        at = self._now()
        updates.update({"updated_at": at, "version": current.version + 1})
        updated = ControlledDocument.model_validate(
            {**current.model_dump(), **cast(dict[str, object], updates)}
        )
        self.repository.update_metadata(
            updated,
            request.expected_version,
            self._audit(
                document=updated,
                actor=normalized_actor,
                action="controlled_document_metadata_changed",
                at=at,
                evidence={
                    "before": current.model_dump(mode="json", exclude={"provenance"}),
                    "after": updated.model_dump(mode="json", exclude={"provenance"}),
                },
            ),
        )
        return updated

    def withdraw(self, document_id: str, expected_version: int, actor: str) -> ControlledDocument:
        """Withdraw a logical document while retaining all immutable evidence."""
        normalized_actor = self._actor(actor)
        current = self.get_document(document_id)
        if current.version != expected_version:
            raise StaleDocumentError("controlled document changed before withdrawal")
        if current.lifecycle_state == DocumentLifecycle.WITHDRAWN:
            raise ValueError("controlled document is already withdrawn")
        at = self._now()
        updated = current.model_copy(
            update={
                "lifecycle_state": DocumentLifecycle.WITHDRAWN,
                "updated_at": at,
                "version": current.version + 1,
            }
        )
        self.repository.withdraw(
            updated,
            expected_version,
            self._audit(
                document=updated,
                actor=normalized_actor,
                action="controlled_document_withdrawn",
                at=at,
                evidence={"from": "ACTIVE", "to": "WITHDRAWN"},
            ),
        )
        return updated

    def verify_integrity(self, document_version_id: str) -> IntegrityResult:
        """Return deterministic read-only integrity evidence without auditing the read."""
        return self.storage.verify(self.get_version(document_version_id))

    def open_download(self, document_version_id: str) -> tuple[DocumentVersion, BinaryIO]:
        """Open exact immutable bytes through the safe managed-storage seam."""
        version = self.get_version(document_version_id)
        return version, self.storage.open_read(version.storage_key)

    def diagnose_storage(self) -> StorageDiagnostic:
        """Separate changed/missing committed files from unreferenced managed files."""
        versions = self.repository.all_versions()
        referenced = self.repository.referenced_storage_keys()
        return StorageDiagnostic(
            committed_file_results=[self.storage.verify(version) for version in versions],
            unreferenced_storage_keys=sorted(set(self.storage.iter_managed_keys()) - referenced),
            symlink_storage_keys=sorted(self.storage.iter_symlink_keys()),
            logical_issues=self.repository.diagnose_logical_integrity(),
        )
