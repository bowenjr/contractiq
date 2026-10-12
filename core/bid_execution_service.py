"""Validated application service for Bid execution overlays and progress notes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from core.bid_execution import (
    BidExecutionDependency,
    BidExecutionNote,
    BidExecutionRecord,
    BidExecutionUpdate,
    ExecutionSubjectKind,
    PrepStatus,
)
from core.bid_execution_repository import BidExecutionRepository, DependencyCycleError
from core.bid_execution_subjects import ExecutionSubjectFacts, ExecutionSubjectResolver
from core.bid_repository import BidRepository
from core.document_repository import DocumentRepository

BidExecutionUpdateData = BidExecutionUpdate | Mapping[str, object]


class BidExecutionService:
    """Validate execution commands before passing atomic writes to the repository."""

    def __init__(
        self,
        repository: BidExecutionRepository,
        bid_repository: BidRepository,
        document_repository: DocumentRepository,
        subject_resolver: ExecutionSubjectResolver,
        *,
        now_factory: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self.repository = repository
        self.bid_repository = bid_repository
        self.document_repository = document_repository
        self.subject_resolver = subject_resolver
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4

    def _now(self) -> datetime:
        now = self._now_factory()
        if now.tzinfo is None:
            raise ValueError(
                "the configured clock returned a time without a timezone; configure a "
                "UTC-aware clock"
            )
        return now.astimezone(UTC)

    @staticmethod
    def _actor(actor: str) -> str:
        normalized = actor.strip()
        if not normalized:
            raise ValueError("actor cannot be blank; provide the current user")
        return normalized

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}-{self._id_factory()}"

    @staticmethod
    def _kind(subject_kind: ExecutionSubjectKind | str) -> ExecutionSubjectKind:
        try:
            return ExecutionSubjectKind(subject_kind)
        except ValueError as exc:
            raise ValueError(
                "unknown execution item type; choose a pre-bid action, returnable, or "
                "manufacturer input"
            ) from exc

    @staticmethod
    def _subject_id(subject_id: str) -> str:
        normalized = subject_id.strip()
        if not normalized:
            raise ValueError("item identifier cannot be blank; reopen the item and try again")
        return normalized

    def get_record(
        self,
        bid_id: str,
        subject_kind: ExecutionSubjectKind | str,
        subject_id: str,
    ) -> BidExecutionRecord | None:
        """Fetch one overlay while guarding against a cross-Bid URL or stale link."""
        kind = self._kind(subject_kind)
        record = self.repository.get_record(kind, self._subject_id(subject_id))
        if record is not None and record.bid_id != bid_id:
            raise ValueError(
                "this execution item belongs to a different Bid; open it from the correct "
                "Bid workspace"
            )
        return record

    def get_overlay_record(
        self,
        bid_id: str,
        subject_kind: ExecutionSubjectKind | str,
        subject_id: str,
    ) -> BidExecutionRecord | None:
        """Alias describing the overlay role explicitly for callers."""
        return self.get_record(bid_id, subject_kind, subject_id)

    def _validate_evidence(self, bid_id: str, document_version_id: str | None) -> None:
        if document_version_id is None:
            return
        version = self.document_repository.get_version(document_version_id)
        if version is None:
            raise ValueError(
                "the selected controlled document version does not exist; choose an existing "
                "version or enter a completion reference"
            )
        document = self.document_repository.get(version.document_id)
        if document is None or document.bid_id != bid_id:
            raise ValueError(
                "the selected controlled document version is not part of this Bid; choose "
                "evidence from this Bid or enter a completion reference"
            )

    @staticmethod
    def _validate_kind_fields(kind: ExecutionSubjectKind, request: BidExecutionUpdate) -> None:
        if kind is ExecutionSubjectKind.WORK_ITEM and any(
            value is not None
            for value in (
                request.internal_due_date,
                request.prep_status,
                request.priority,
                request.blocked_reason,
                request.completion_evidence,
                request.evidence_document_version_id,
            )
        ):
            raise ValueError(
                "pre-bid action status, priority, due date, and completion evidence belong to "
                "My Work; update them through the work-item screen"
            )
        if kind is ExecutionSubjectKind.MANUFACTURER_INPUT and any(
            value is not None
            for value in (
                request.prep_status,
                request.blocked_reason,
                request.completion_evidence,
                request.evidence_document_version_id,
            )
        ):
            raise ValueError(
                "manufacturer-input status and evidence come from verification; update them "
                "on the manufacturer-requirement screen"
            )

    @staticmethod
    def _validate_returnable_transition(
        previous: PrepStatus,
        request: BidExecutionUpdate,
    ) -> None:
        target = request.prep_status or PrepStatus.NOT_STARTED
        if target is PrepStatus.COMPLETE and not (
            request.completion_evidence or request.evidence_document_version_id
        ):
            raise ValueError(
                "a returnable cannot be marked Complete without evidence; enter a completion "
                "reference or choose a controlled document version"
            )
        if target is PrepStatus.BLOCKED and request.blocked_reason is None:
            raise ValueError(
                "a returnable cannot be marked Blocked without a reason; describe what is "
                "blocking preparation"
            )
        if (
            target is PrepStatus.NOT_REQUIRED
            and previous is not PrepStatus.NOT_REQUIRED
            and request.note is None
        ):
            raise ValueError(
                "a returnable cannot be marked Not required without a note; add the reason as "
                "a progress note"
            )

    def set_record(
        self,
        bid_id: str,
        subject_kind: ExecutionSubjectKind | str,
        subject_id: str,
        data: BidExecutionUpdateData,
        actor: str,
    ) -> BidExecutionRecord:
        """Set the complete overlay state with validation and optimistic locking."""
        request = BidExecutionUpdate.model_validate(data)
        kind = self._kind(subject_kind)
        normalized_subject_id = self._subject_id(subject_id)
        normalized_actor = self._actor(actor)
        bid = self.bid_repository.get_bid(bid_id)
        if bid is None:
            raise ValueError("Bid not found; return to the Bid list and choose an existing Bid")
        self.subject_resolver.resolve(bid_id, kind, normalized_subject_id)
        self._validate_kind_fields(kind, request)
        if (
            kind is not ExecutionSubjectKind.WORK_ITEM
            and request.internal_due_date is not None
            and request.internal_due_date > bid.customer_due_date
        ):
            raise ValueError(
                f"the internal due date is after the customer deadline of "
                f"{bid.customer_due_date.isoformat()}; choose a date on or before the customer "
                "deadline"
            )
        self._validate_evidence(bid_id, request.evidence_document_version_id)

        existing = self.get_record(bid_id, kind, normalized_subject_id)
        if kind is ExecutionSubjectKind.RETURNABLE:
            previous = (
                existing.prep_status
                if existing is not None and existing.prep_status is not None
                else PrepStatus.NOT_STARTED
            )
            self._validate_returnable_transition(previous, request)

        now = self._now()
        desired = BidExecutionRecord(
            execution_id=(existing.execution_id if existing is not None else self._new_id("BER")),
            bid_id=bid_id,
            subject_kind=kind,
            subject_id=normalized_subject_id,
            responsible=request.responsible,
            contributors=request.contributors,
            internal_due_date=request.internal_due_date,
            prep_status=request.prep_status,
            priority=request.priority,
            blocked_reason=request.blocked_reason,
            completion_evidence=request.completion_evidence,
            evidence_document_version_id=request.evidence_document_version_id,
            version=existing.version + 1 if existing is not None else 1,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
            updated_by=normalized_actor,
        )
        note = (
            BidExecutionNote(
                note_id=self._new_id("BEN"),
                bid_id=bid_id,
                subject_kind=kind,
                subject_id=normalized_subject_id,
                body=request.note,
                created_at=now,
                created_by=normalized_actor,
            )
            if request.note is not None
            else None
        )
        return self.repository.save_record(
            desired,
            request.expected_version,
            normalized_actor,
            note=note,
        )

    def set_overlay_record(
        self,
        bid_id: str,
        subject_kind: ExecutionSubjectKind | str,
        subject_id: str,
        data: BidExecutionUpdateData,
        actor: str,
    ) -> BidExecutionRecord:
        """Alias for setting the user-authored execution overlay."""
        return self.set_record(bid_id, subject_kind, subject_id, data, actor)

    def add_note(
        self,
        bid_id: str,
        subject_kind: ExecutionSubjectKind | str,
        subject_id: str,
        body: str,
        actor: str,
    ) -> BidExecutionNote:
        """Append an immutable progress note with atomic audit evidence."""
        if self.bid_repository.get_bid(bid_id) is None:
            raise ValueError("Bid not found; return to the Bid list and choose an existing Bid")
        normalized_actor = self._actor(actor)
        kind = self._kind(subject_kind)
        normalized_subject_id = self._subject_id(subject_id)
        self.subject_resolver.resolve(bid_id, kind, normalized_subject_id)
        note = BidExecutionNote(
            note_id=self._new_id("BEN"),
            bid_id=bid_id,
            subject_kind=kind,
            subject_id=normalized_subject_id,
            body=body,
            created_at=self._now(),
            created_by=normalized_actor,
        )
        return self.repository.add_note(note)

    def list_notes(
        self,
        bid_id: str,
        subject_kind: ExecutionSubjectKind | str,
        subject_id: str,
    ) -> list[BidExecutionNote]:
        """Return notes only for an existing subject in the requested Bid."""
        kind = self._kind(subject_kind)
        normalized_subject_id = self._subject_id(subject_id)
        self.subject_resolver.resolve(bid_id, kind, normalized_subject_id)
        notes = self.repository.list_notes(kind, normalized_subject_id)
        if any(note.bid_id != bid_id for note in notes):
            raise ValueError(
                "these progress notes belong to a different Bid; open them from the correct "
                "Bid workspace"
            )
        return notes

    @staticmethod
    def _cycle_message(
        item: ExecutionSubjectFacts,
        prerequisite: ExecutionSubjectFacts,
        path: tuple[tuple[ExecutionSubjectKind, str], ...],
        resolver: ExecutionSubjectResolver,
    ) -> str:
        titles = [prerequisite.title]
        for kind, subject_id in path[1:-1]:
            titles.append(resolver.resolve(item.bid_id, kind, subject_id).title)
        titles.append(item.title)
        chain = ", which already depends on ".join(titles)
        return (
            f"{item.title} would depend on {chain}; remove an existing dependency in this "
            "loop before adding it"
        )

    def add_dependency(
        self,
        bid_id: str,
        item_kind: ExecutionSubjectKind | str,
        item_id: str,
        prerequisite_kind: ExecutionSubjectKind | str,
        prerequisite_id: str,
        note: str | None,
        actor: str,
    ) -> BidExecutionDependency:
        """Add an audited same-Bid dependency while preventing duplicate or cyclic edges."""
        if self.bid_repository.get_bid(bid_id) is None:
            raise ValueError("Bid not found; return to the Bid list and choose an existing Bid")
        normalized_actor = self._actor(actor)
        normalized_item_kind = self._kind(item_kind)
        normalized_prerequisite_kind = self._kind(prerequisite_kind)
        normalized_item_id = self._subject_id(item_id)
        normalized_prerequisite_id = self._subject_id(prerequisite_id)
        item = self.subject_resolver.resolve(bid_id, normalized_item_kind, normalized_item_id)
        prerequisite = self.subject_resolver.resolve(
            bid_id, normalized_prerequisite_kind, normalized_prerequisite_id
        )
        if item.kind is prerequisite.kind and item.subject_id == prerequisite.subject_id:
            raise ValueError(
                f"{item.title} cannot depend on itself; choose a different prerequisite"
            )
        dependency = BidExecutionDependency(
            dependency_id=self._new_id("BED"),
            bid_id=bid_id,
            item_kind=item.kind,
            item_id=item.subject_id,
            prerequisite_kind=prerequisite.kind,
            prerequisite_id=prerequisite.subject_id,
            note=note,
            created_at=self._now(),
            created_by=normalized_actor,
        )
        try:
            return self.repository.add_dependency(dependency)
        except DependencyCycleError as exc:
            raise ValueError(
                self._cycle_message(item, prerequisite, exc.path, self.subject_resolver)
            ) from exc

    def remove_dependency(
        self,
        bid_id: str,
        dependency_id: str,
        actor: str,
    ) -> BidExecutionDependency:
        """Remove one dependency once after revalidating both authoritative subjects."""
        normalized_actor = self._actor(actor)
        dependency = next(
            (
                value
                for value in self.repository.list_dependencies(bid_id)
                if value.dependency_id == dependency_id
            ),
            None,
        )
        if dependency is None:
            raise ValueError(
                "dependency was not found in this Bid; refresh the item and choose an existing "
                "dependency"
            )
        self.subject_resolver.resolve(bid_id, dependency.item_kind, dependency.item_id)
        self.subject_resolver.resolve(
            bid_id, dependency.prerequisite_kind, dependency.prerequisite_id
        )
        return self.repository.remove_dependency(
            bid_id,
            dependency_id,
            normalized_actor,
            removed_at=self._now(),
        )
