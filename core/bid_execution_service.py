"""Validated application service for Bid execution overlays and progress notes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from core.bid_execution import (
    BidExecutionNote,
    BidExecutionRecord,
    BidExecutionUpdate,
    ExecutionSubjectKind,
    PrepStatus,
)
from core.bid_execution_repository import BidExecutionRepository
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
        *,
        now_factory: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self.repository = repository
        self.bid_repository = bid_repository
        self.document_repository = document_repository
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
        note = BidExecutionNote(
            note_id=self._new_id("BEN"),
            bid_id=bid_id,
            subject_kind=self._kind(subject_kind),
            subject_id=self._subject_id(subject_id),
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
        """Return notes for a subject and reject cross-Bid overlay access."""
        kind = self._kind(subject_kind)
        normalized_subject_id = self._subject_id(subject_id)
        record = self.repository.get_record(kind, normalized_subject_id)
        if record is not None and record.bid_id != bid_id:
            raise ValueError(
                "this execution item belongs to a different Bid; open it from the correct "
                "Bid workspace"
            )
        notes = self.repository.list_notes(kind, normalized_subject_id)
        if any(note.bid_id != bid_id for note in notes):
            raise ValueError(
                "these progress notes belong to a different Bid; open them from the correct "
                "Bid workspace"
            )
        return notes
