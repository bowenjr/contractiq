"""Validated service boundary for manual requirements and compliance workflow."""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID, uuid4

from core.bid_repository import BidRepository
from core.document_control import ControlledDocumentIntegrityError
from core.document_repository import DocumentRepository
from core.enums import Actor
from core.requirement_coverage import RequirementCoverage, calculate_requirement_coverage
from core.requirement_repository import (
    RequirementNotFoundError,
    RequirementRepository,
    RequirementSourceError,
    StaleRequirementError,
)
from core.requirements import (
    ATTENTION_SIGNIFICANCE,
    Requirement,
    RequirementCategory,
    RequirementCreate,
    RequirementDetail,
    RequirementLifecycle,
    RequirementMetadataEdit,
    RequirementOrigin,
    RequirementReviewDecision,
    RequirementReviewState,
    RequirementSignificance,
    RequirementSourceCandidate,
    RequirementSourceChoices,
    RequirementWithdraw,
    RequirementWorkflowUpdate,
    RequirementWorkState,
    ResponseDisposition,
)
from core.schemas import AuditEntry, Provenance

RequirementCreateData = RequirementCreate | Mapping[str, object]
RequirementMetadataData = RequirementMetadataEdit | Mapping[str, object]
RequirementWorkflowData = RequirementWorkflowUpdate | Mapping[str, object]
RequirementReviewData = RequirementReviewDecision | Mapping[str, object]
RequirementWithdrawData = RequirementWithdraw | Mapping[str, object]


class RequirementService:
    """The only supported mutation boundary for authoritative requirements."""

    def __init__(
        self,
        repository: RequirementRepository,
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
    def _source_location(request: RequirementCreate) -> str | None:
        parts: list[str] = []
        if request.source_clause is not None:
            parts.append(f"section {request.source_clause}")
        if request.source_page_start is not None:
            page = str(request.source_page_start)
            if request.source_page_end is not None:
                page = f"{page}-{request.source_page_end}"
            parts.append(f"page {page}")
        if request.source_locator_note is not None:
            parts.append(request.source_locator_note)
        return "; ".join(parts) if parts else None

    def _audit(
        self,
        *,
        requirement: Requirement,
        actor: str,
        action: str,
        at: datetime,
        evidence: Mapping[str, object],
    ) -> AuditEntry:
        return AuditEntry(
            entry_id=self._new_id("AUD"),
            bid_id=requirement.bid_id,
            actor=actor,
            action=action,
            detail=json.dumps(
                {
                    "requirement_id": requirement.requirement_id,
                    "operation": action,
                    "evidence": dict(evidence),
                },
                sort_keys=True,
                default=str,
            ),
            timestamp=at,
        )

    def _validated_source(
        self,
        bid_id: str,
        document_version_id: str | None,
    ) -> tuple[str | None, str | None]:
        if document_version_id is None:
            return None, None
        version = self.document_repository.get_version(document_version_id)
        if version is None:
            raise RequirementSourceError("Controlled source version was not found")
        try:
            document = self.document_repository.get(version.document_id)
        except ControlledDocumentIntegrityError as exc:
            raise RequirementSourceError(
                "Controlled source is unavailable pending integrity review"
            ) from exc
        if document is None or document.bid_id != bid_id:
            raise RequirementSourceError("Controlled source version does not belong to this bid")
        issues = self.document_repository.diagnose_logical_integrity()
        if any(issue.document_id == document.document_id for issue in issues):
            raise RequirementSourceError(
                "Controlled source is unavailable pending integrity review"
            )
        return document.document_id, version.document_version_id

    def create_requirement(self, data: RequirementCreateData, actor: str) -> Requirement:
        """Validate source ownership, then atomically create requirement and audit."""
        request = RequirementCreate.model_validate(data)
        normalized_actor = self._actor(actor)
        if self.bid_repository.get_bid(request.bid_id) is None:
            raise ValueError(f"Bid not found: {request.bid_id}")
        source_document_id, source_version_id = self._validated_source(
            request.bid_id,
            request.source_document_version_id,
        )
        at = self._now()
        requirement_id = self._new_id("REQ")
        provenance = Provenance(
            created_by=Actor.HUMAN,
            agent_name=normalized_actor,
            source_document_id=source_document_id,
            source_location=self._source_location(request),
            created_at=at,
            human_confirmed=True,
            confirmed_by=normalized_actor,
            confirmed_at=at,
        )
        requirement = Requirement(
            requirement_id=requirement_id,
            bid_id=request.bid_id,
            title=request.title,
            statement=request.statement,
            interpretation=request.interpretation,
            origin=request.origin,
            category=request.category,
            significance=request.significance,
            lifecycle_stage=request.lifecycle_stage,
            lifecycle_state=RequirementLifecycle.ACTIVE,
            owner=request.owner,
            due_date=request.due_date,
            source_document_id=source_document_id,
            source_document_version_id=source_version_id,
            source_clause=request.source_clause,
            source_page_start=request.source_page_start,
            source_page_end=request.source_page_end,
            source_locator_note=request.source_locator_note,
            source_excerpt=request.source_excerpt,
            disposition=ResponseDisposition.UNASSESSED,
            work_state=RequirementWorkState.OPEN,
            review_state=RequirementReviewState.NOT_REVIEWED,
            created_at=at,
            updated_at=at,
            version=1,
            provenance=provenance,
        )
        self.repository.create(
            requirement,
            self._audit(
                requirement=requirement,
                actor=normalized_actor,
                action="requirement_created",
                at=at,
                evidence={
                    "origin": requirement.origin.value,
                    "category": requirement.category.value,
                    "significance": requirement.significance.value,
                    "source_document_id": source_document_id,
                    "source_document_version_id": source_version_id,
                    "source_clause": requirement.source_clause,
                    "source_page_start": requirement.source_page_start,
                    "source_page_end": requirement.source_page_end,
                    "source_locator_note": requirement.source_locator_note,
                    "source_excerpt_length": len(requirement.source_excerpt or ""),
                },
            ),
        )
        return requirement

    def get_requirement(self, requirement_id: str) -> Requirement:
        """Fetch one requirement or raise a stable typed error."""
        requirement = self.repository.get(requirement_id)
        if requirement is None:
            raise RequirementNotFoundError(f"Requirement not found: {requirement_id}")
        return requirement

    @staticmethod
    def _require_active(requirement: Requirement) -> None:
        if requirement.lifecycle_state != RequirementLifecycle.ACTIVE:
            raise ValueError("closed requirements cannot be modified")

    @staticmethod
    def _require_version(requirement: Requirement, expected_version: int) -> None:
        if requirement.version != expected_version:
            raise StaleRequirementError(
                f"Stale requirement version: expected {expected_version}, "
                f"current {requirement.version}"
            )

    def update_metadata(
        self,
        requirement_id: str,
        data: RequirementMetadataData,
        actor: str,
    ) -> Requirement:
        """Update descriptive/assignment fields without changing source identity."""
        request = RequirementMetadataEdit.model_validate(data)
        normalized_actor = self._actor(actor)
        current = self.get_requirement(requirement_id)
        self._require_active(current)
        self._require_version(current, request.expected_version)
        updates = request.model_dump(exclude_unset=True)
        updates.pop("expected_version", None)
        defining_fields = {
            "title",
            "statement",
            "interpretation",
            "category",
            "significance",
            "lifecycle_stage",
        }
        substantive_change = any(
            field in updates and updates[field] != getattr(current, field)
            for field in defining_fields
        )
        at = self._now()
        updated = Requirement.model_validate(
            {
                **current.model_dump(),
                **cast(dict[str, object], updates),
                "review_state": (
                    RequirementReviewState.NOT_REVIEWED
                    if substantive_change
                    else current.review_state
                ),
                "reviewer": None if substantive_change else current.reviewer,
                "review_note": None if substantive_change else current.review_note,
                "updated_at": at,
                "version": current.version + 1,
            }
        )
        changed_fields = sorted(updates)
        self.repository.update_metadata(
            updated,
            request.expected_version,
            self._audit(
                requirement=updated,
                actor=normalized_actor,
                action="requirement_metadata_changed",
                at=at,
                evidence={
                    "changed_fields": changed_fields,
                    "title": updated.title,
                    "owner": updated.owner,
                    "due_date": updated.due_date,
                    "category": updated.category.value,
                    "significance": updated.significance.value,
                    "review_reset": substantive_change,
                },
            ),
        )
        return updated

    def update_workflow(
        self,
        requirement_id: str,
        data: RequirementWorkflowData,
        actor: str,
    ) -> Requirement:
        """Update response/work state and invalidate stale review of changed content."""
        request = RequirementWorkflowUpdate.model_validate(data)
        normalized_actor = self._actor(actor)
        current = self.get_requirement(requirement_id)
        self._require_active(current)
        self._require_version(current, request.expected_version)
        substantive_change = (
            request.disposition != current.disposition
            or request.response_text != current.response_text
            or request.evidence_description != current.evidence_description
            or request.proposal_location != current.proposal_location
        )
        at = self._now()
        updated = Requirement.model_validate(
            {
                **current.model_dump(),
                **request.model_dump(exclude={"expected_version"}),
                "review_state": (
                    RequirementReviewState.NOT_REVIEWED
                    if substantive_change
                    else current.review_state
                ),
                "reviewer": None if substantive_change else current.reviewer,
                "review_note": None if substantive_change else current.review_note,
                "updated_at": at,
                "version": current.version + 1,
            }
        )
        self.repository.update_workflow(
            updated,
            request.expected_version,
            self._audit(
                requirement=updated,
                actor=normalized_actor,
                action="requirement_response_workflow_changed",
                at=at,
                evidence={
                    "from_disposition": current.disposition.value,
                    "to_disposition": updated.disposition.value,
                    "from_work_state": current.work_state.value,
                    "to_work_state": updated.work_state.value,
                    "response_text_length": len(updated.response_text or ""),
                    "evidence_description_length": len(updated.evidence_description or ""),
                    "proposal_location_set": updated.proposal_location is not None,
                    "review_reset": substantive_change,
                },
            ),
        )
        return updated

    def record_review(
        self,
        requirement_id: str,
        data: RequirementReviewData,
        actor: str,
    ) -> Requirement:
        """Record an eligible independent decision; changes required becomes actionable."""
        request = RequirementReviewDecision.model_validate(data)
        normalized_actor = self._actor(actor)
        current = self.get_requirement(requirement_id)
        self._require_active(current)
        self._require_version(current, request.expected_version)
        if current.disposition == ResponseDisposition.UNASSESSED:
            raise ValueError("review requires an assessed disposition")
        if current.work_state not in {
            RequirementWorkState.READY_FOR_REVIEW,
            RequirementWorkState.COMPLETE,
        }:
            raise ValueError("review requires READY_FOR_REVIEW or COMPLETE work")
        work_state: RequirementWorkState = current.work_state
        if request.review_state == RequirementReviewState.CHANGES_REQUIRED:
            work_state = RequirementWorkState.IN_PROGRESS
        at = self._now()
        updated = Requirement.model_validate(
            {
                **current.model_dump(),
                "review_state": request.review_state,
                "reviewer": request.reviewer,
                "review_note": request.review_note,
                "work_state": work_state,
                "updated_at": at,
                "version": current.version + 1,
            }
        )
        self.repository.record_review(
            updated,
            request.expected_version,
            self._audit(
                requirement=updated,
                actor=normalized_actor,
                action="requirement_review_recorded",
                at=at,
                evidence={
                    "from_review_state": current.review_state.value,
                    "to_review_state": updated.review_state.value,
                    "reviewer": updated.reviewer,
                    "review_note_length": len(updated.review_note or ""),
                    "resulting_work_state": updated.work_state.value,
                },
            ),
        )
        return updated

    def withdraw(
        self,
        requirement_id: str,
        data: RequirementWithdrawData,
        actor: str,
    ) -> Requirement:
        """Withdraw a requirement while preserving source, workflow, and audit history."""
        request = RequirementWithdraw.model_validate(data)
        normalized_actor = self._actor(actor)
        current = self.get_requirement(requirement_id)
        self._require_active(current)
        self._require_version(current, request.expected_version)
        at = self._now()
        updated = current.model_copy(
            update={
                "lifecycle_state": RequirementLifecycle.WITHDRAWN,
                "updated_at": at,
                "version": current.version + 1,
            }
        )
        self.repository.withdraw(
            updated,
            request.expected_version,
            self._audit(
                requirement=updated,
                actor=normalized_actor,
                action="requirement_withdrawn",
                at=at,
                evidence={"from": "ACTIVE", "to": "WITHDRAWN"},
            ),
        )
        return updated

    def list_requirements(
        self,
        *,
        bid_id: str | None = None,
        origin: RequirementOrigin | None = None,
        category: RequirementCategory | None = None,
        significance: RequirementSignificance | None = None,
        lifecycle: RequirementLifecycle | None = None,
        disposition: ResponseDisposition | None = None,
        work_state: RequirementWorkState | None = None,
        review_state: RequirementReviewState | None = None,
        owner: str | None = None,
        due_state: str | None = None,
        attention_only: bool = False,
        exception_only: bool = False,
        as_of_date: date,
    ) -> list[Requirement]:
        """Return filtered requirements in attention/due/title/ID order."""
        records = self.repository.list(
            bid_id=bid_id,
            origin=origin,
            category=category,
            significance=significance,
            lifecycle=lifecycle,
            disposition=disposition,
            work_state=work_state,
            review_state=review_state,
            owner=owner,
        )
        if due_state == "OVERDUE":
            records = [
                item
                for item in records
                if item.lifecycle_state == RequirementLifecycle.ACTIVE
                and item.due_date is not None
                and item.due_date < as_of_date
                and not item.fully_closed
            ]
        elif due_state == "DUE_TODAY":
            records = [
                item
                for item in records
                if item.lifecycle_state == RequirementLifecycle.ACTIVE
                and item.due_date == as_of_date
                and not item.fully_closed
            ]
        elif due_state == "UNSCHEDULED":
            records = [item for item in records if item.due_date is None]
        if attention_only:
            records = [
                item
                for item in records
                if item.lifecycle_state == RequirementLifecycle.ACTIVE
                and item.significance in ATTENTION_SIGNIFICANCE
                and not item.fully_closed
            ]
        if exception_only:
            records = [
                item
                for item in records
                if item.lifecycle_state == RequirementLifecycle.ACTIVE and item.is_exception
            ]
        records.sort(key=lambda item: self._sort_key(item, as_of_date))
        return records

    @staticmethod
    def _sort_key(requirement: Requirement, as_of_date: date) -> tuple[object, ...]:
        active = requirement.lifecycle_state == RequirementLifecycle.ACTIVE
        overdue = (
            active
            and requirement.due_date is not None
            and requirement.due_date < as_of_date
            and not requirement.fully_closed
        )
        attention = (
            active
            and requirement.significance in ATTENTION_SIGNIFICANCE
            and not requirement.fully_closed
        )
        significance_rank = {
            RequirementSignificance.DISQUALIFYING: 0,
            RequirementSignificance.MANDATORY: 1,
            RequirementSignificance.SCORED: 2,
            RequirementSignificance.INFORMATIONAL: 3,
        }
        return (
            0 if overdue else 1,
            0 if attention else 1,
            significance_rank[requirement.significance],
            requirement.due_date or date.max,
            requirement.title.casefold(),
            requirement.requirement_id,
        )

    def coverage(self, *, bid_id: str | None, as_of_date: date) -> RequirementCoverage:
        """Calculate deterministic coverage for a bid or the full portfolio."""
        return calculate_requirement_coverage(
            self.repository.list(bid_id=bid_id),
            as_of_date,
        )

    def source_choices(self, bid_id: str) -> RequirementSourceChoices:
        """Return healthy, bid-owned immutable versions for safe source selection."""
        if self.bid_repository.get_bid(bid_id) is None:
            raise ValueError(f"Bid not found: {bid_id}")
        entries = self.document_repository.list_register_entries(bid_id=bid_id)
        available: list[RequirementSourceCandidate] = []
        excluded = 0
        for entry in entries:
            if entry.document is None or entry.logical_issues:
                excluded += 1
                continue
            for version in self.document_repository.list_versions(entry.document_id):
                available.append(
                    RequirementSourceCandidate(
                        bid_id=entry.document.bid_id,
                        document_id=entry.document.document_id,
                        document_title=entry.document.title,
                        document_lifecycle=entry.document.lifecycle_state.value,
                        document_version_id=version.document_version_id,
                        version_label=version.version_label,
                        version_state=version.version_state.value,
                        sha256_abbreviation=version.sha256_digest[:12],
                    )
                )
        available.sort(
            key=lambda item: (
                item.document_title.casefold(),
                0 if item.version_state == "CURRENT" else 1,
                item.version_label.casefold(),
                item.document_version_id,
            )
        )
        return RequirementSourceChoices(
            available=available,
            excluded_document_count=excluded,
            exclusion_message=(
                "Some controlled sources are unavailable pending integrity review."
                if excluded
                else None
            ),
        )

    def detail(self, requirement_id: str) -> RequirementDetail:
        """Return immutable source context without reading or rendering file bytes."""
        requirement = self.get_requirement(requirement_id)
        if requirement.source_document_version_id is None:
            return RequirementDetail(requirement=requirement)
        version = self.document_repository.get_version(requirement.source_document_version_id)
        if version is None:
            return RequirementDetail(
                requirement=requirement,
                source_context_message="Source version requires operator integrity review.",
            )
        try:
            document = self.document_repository.get(requirement.source_document_id or "")
        except ControlledDocumentIntegrityError:
            document = None
        if document is None:
            return RequirementDetail(
                requirement=requirement,
                source_context_message="Source document requires operator integrity review.",
            )
        return RequirementDetail(
            requirement=requirement,
            source=RequirementSourceCandidate(
                bid_id=document.bid_id,
                document_id=document.document_id,
                document_title=document.title,
                document_lifecycle=document.lifecycle_state.value,
                document_version_id=version.document_version_id,
                version_label=version.version_label,
                version_state=version.version_state.value,
                sha256_abbreviation=version.sha256_digest[:12],
            ),
        )

    def audit_history(self, requirement_id: str) -> list[AuditEntry]:
        """Return only audit entries whose structured payload names this requirement."""
        requirement = self.get_requirement(requirement_id)
        history: list[AuditEntry] = []
        for entry in self.bid_repository.list_audit(requirement.bid_id):
            if not entry.action.startswith("requirement_"):
                continue
            try:
                payload = json.loads(entry.detail)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("requirement_id") == requirement_id:
                history.append(entry)
        history.sort(key=lambda item: (item.timestamp, item.entry_id), reverse=True)
        return history
