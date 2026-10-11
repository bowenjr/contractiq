"""OPS-13A application service — orchestrates discovery, review and confirmation.

Confirmed proposals become real `Deliverable` rows through the existing create -> add_link ->
activate repository sequence, committed atomically with the acceptance review event. Rejected
and deferred proposals only ever gain a review event; they never become an obligation and never
touch readiness.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from core.bid_package_repository import BidPackageRepository
from core.date_integrity import (
    DUE_DATE_BASIS_MARKER,
    DueDateBasis,
    classify_due_date,
    evidence_window,
    locate_quote,
)
from core.deliverable_discovery import (
    AddendumDiscoveryFlag,
    DeliverableProposal,
    DiscoveryCriticality,
    DiscoveryRun,
    ExtractionStatus,
    ExtractionUnit,
    LocatorType,
    ProposalReviewDecision,
    ProposalReviewEvent,
    RunStatus,
    SourceCitation,
    SourcePromotion,
    normalize_dedup_key,
)
from core.deliverable_discovery_collisions import (
    DiscoveryCollision,
    ExistingObligation,
    base_dedup_key,
    collision_dedup_key,
    collisions_between,
    compare_obligations,
    facts_from_proposal,
)
from core.deliverable_discovery_destinations import (
    AcceptedRecord,
    DestinationWriter,
    FindingFacts,
    accept_into_record,
)
from core.deliverable_discovery_evidence import parse_date
from core.deliverable_discovery_extraction import (
    RawUnit,
    UnsupportedMediaTypeError,
    extract_units,
    locator_type_for_filename,
)
from core.deliverable_discovery_llm import (
    LLMClientProtocol,
    ProposedCandidate,
    analyse_units,
)
from core.deliverable_discovery_outcomes import (
    DiscoveryOutcome,
    FileAnalysisEvidence,
    derive_discovery_outcomes,
)
from core.deliverable_discovery_repository import DeliverableDiscoveryRepository
from core.deliverable_repository import DeliverableRepository
from core.deliverable_service import DeliverableService
from core.deliverables import (
    Deliverable,
    DeliverableCriticality,
    DeliverableDirection,
    DeliverableLink,
    DeliverableRelation,
    DeliverableTargetType,
    DueBasis,
    LifecyclePhase,
)
from core.document_service import DocumentService
from core.enums import Actor
from core.requirement_lifecycle import (
    Confidence,
    DestinationKind,
    DiscoveryLifecycle,
    ProposalClassification,
    RequirementStatus,
    deliverable_criticality,
    deliverable_phase,
    destination_for,
    is_acceptable,
    needs_criticality,
)
from core.schemas import Provenance

SCHEMA_VERSION = "ops-13a-discovery-export-v1"


class DiscoveryUnavailableError(ValueError):
    """No received files are currently marked Include for analysis."""


class ImportRejectedError(ValueError):
    """The import package failed schema or source-provenance validation."""


@dataclass(frozen=True)
class ProposalDecisionInput:
    proposal_id: str
    decision: ProposalReviewDecision
    rationale: str | None = None
    criticality: DeliverableCriticality | None = None
    owner: str | None = None
    recipient: str | None = None
    due_date: date | None = None
    """A due date the reviewer confirmed against the source while accepting."""
    package_id: str | None = None
    """The manufacturer package a manufacturer input is registered in, when the Bid has several."""


class ExportSourceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_version_id: str | None = Field(default=None, min_length=1)
    received_file_id: str | None = Field(default=None, min_length=1)
    locator_type: LocatorType
    locator: str
    excerpt: str = Field(max_length=2000)

    @model_validator(mode="after")
    def source_identity(self) -> ExportSourceCitation:
        if (self.document_version_id is None) == (self.received_file_id is None):
            raise ValueError(
                "export source must identify exactly one of document_version_id or received_file_id"
            )
        return self

    @property
    def source_citation(self) -> SourceCitation:
        return SourceCitation.model_validate(self.model_dump())


class ExportProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    category: str
    criticality: DiscoveryCriticality
    condition_text: str | None = None
    due_date: date | None = None
    requested_format: str | None = None
    requested_recipient: str | None = None
    proposed_owner: str | None = None
    sources: tuple[ExportSourceCitation, ...] = Field(min_length=1)


class ExportPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str
    bid_id: str
    exported_at: datetime
    discovery_outcome: DiscoveryOutcome | None = None
    proposals: tuple[ExportProposal, ...]


def _document_label(file_row: Mapping[str, Any], filename: str) -> str:
    """The file's relative path (so the folder names the document's role), else its name."""
    return str(file_row.get("original_relative_path") or filename)


def _managed_copy_failure(locator_type: LocatorType, exc: Exception) -> RawUnit:
    return RawUnit(
        locator_type=locator_type.value,
        locator="file",
        text="",
        readable=False,
        failure_reason=(
            "managed copy could not be opened or verified "
            f"({type(exc).__name__}); re-register the file"
        )[:500],
    )


class DeliverableDiscoveryService:
    def __init__(
        self,
        repository: DeliverableDiscoveryRepository,
        bid_package_repository: BidPackageRepository,
        document_service: DocumentService,
        deliverable_repository: DeliverableRepository,
        deliverable_service: DeliverableService,
        llm_client: LLMClientProtocol,
    ) -> None:
        self.repository = repository
        self.bid_package_repository = bid_package_repository
        self.document_service = document_service
        self.deliverable_repository = deliverable_repository
        self.deliverable_service = deliverable_service
        self.llm_client = llm_client
        self.destinations: DestinationWriter | None = None
        """Set at start-up; routes classified findings to their register."""

    # -- discovery run -----------------------------------------------------------------

    def run_discovery(self, bid_id: str, operation_id: str, actor: str) -> dict[str, Any]:
        existing = self.repository.existing_run(bid_id, operation_id)
        if existing is not None:
            return existing

        eligible_files = self.bid_package_repository.eligible_received_files(bid_id)
        if not eligible_files:
            raise DiscoveryUnavailableError(
                'No received files are available for discovery. Mark files "Include for '
                'analysis" first.'
            )

        snapshot = self.bid_package_repository.current_snapshot(bid_id)
        snapshot_id = str(snapshot["snapshot_id"]) if snapshot is not None else None

        started_at = datetime.now(UTC)
        run_id = f"DDN-{uuid4().hex}"
        extractions: list[ExtractionUnit] = []
        raw_units_by_extraction: list[tuple[str, RawUnit]] = []
        file_evidence: list[FileAnalysisEvidence] = []
        for file_row in eligible_files:
            received_file_id = str(file_row["file_id"])
            release_id = str(file_row["release_id"])
            filename = str(file_row["original_filename"])
            try:
                file_locator_type = LocatorType(locator_type_for_filename(filename))
            except UnsupportedMediaTypeError:
                file_evidence.append(
                    FileAnalysisEvidence(
                        received_file_id=received_file_id,
                        filename=filename,
                        supported=False,
                    )
                )
                continue
            extraction_completed = False
            try:
                handle, evidence = self.bid_package_repository.open_received_file(
                    bid_id, release_id, received_file_id
                )
            except Exception as exc:
                raw_units = [_managed_copy_failure(file_locator_type, exc)]
            else:
                try:
                    try:
                        data = handle.read()
                    except Exception as exc:
                        raw_units = [_managed_copy_failure(file_locator_type, exc)]
                    else:
                        try:
                            raw_units = [
                                replace(unit, document=_document_label(file_row, filename))
                                for unit in extract_units(
                                    str(evidence["original_filename"]),
                                    str(evidence["detected_media_type"])
                                    if evidence["detected_media_type"] is not None
                                    else None,
                                    io.BytesIO(data),
                                )
                            ]
                            extraction_completed = True
                        except ImportError:
                            raise
                        except UnsupportedMediaTypeError:
                            raw_units = []
                        except Exception as exc:
                            raw_units = [_managed_copy_failure(file_locator_type, exc)]
                finally:
                    try:
                        handle.close()
                    except Exception:
                        pass
            passage_ids: list[str] = []
            unreadable_reasons: list[str] = []
            for raw in raw_units:
                if raw.readable and not raw.text.strip():
                    continue
                extraction_id = f"DDX-{uuid4().hex}"
                status = ExtractionStatus.OK if raw.readable else ExtractionStatus.UNREADABLE
                extractions.append(
                    ExtractionUnit(
                        extraction_id=extraction_id,
                        bid_id=bid_id,
                        run_id=run_id,
                        received_file_id=received_file_id,
                        locator_type=LocatorType(raw.locator_type),
                        locator=raw.locator,
                        text_excerpt=raw.text if raw.readable else "",
                        char_count=len(raw.text),
                        status=status,
                        failure_reason=raw.failure_reason,
                        created_at=started_at,
                    )
                )
                if raw.readable:
                    raw_units_by_extraction.append((extraction_id, raw))
                    passage_ids.append(extraction_id)
                elif raw.failure_reason:
                    unreadable_reasons.append(raw.failure_reason)

            file_evidence.append(
                FileAnalysisEvidence(
                    received_file_id=received_file_id,
                    filename=filename,
                    extraction_completed=extraction_completed,
                    passage_ids=tuple(passage_ids),
                    unreadable_reasons=tuple(unreadable_reasons),
                )
            )

        analysis = analyse_units(self.llm_client, raw_units_by_extraction)
        candidates = list(analysis.candidates)
        rejected_count = analysis.invalid_count
        run_outcome, file_outcomes = derive_discovery_outcomes(
            run_id, file_evidence, analysis.outcomes
        )

        extraction_lookup = {unit.extraction_id: unit for unit in extractions}
        proposals = _consolidate(
            bid_id,
            run_id,
            started_at,
            candidates,
            extraction_lookup,
            self.repository.date_triggers(bid_id),
        )
        split = split_known_duplicates(
            proposals,
            self.repository.existing_dedup_index(bid_id),
            self.repository.existing_collision_index(bid_id),
        )
        proposals, appends = split
        collisions = [*collisions_between(proposals), *split.collisions]

        completed_at = datetime.now(UTC)
        run = DiscoveryRun(
            run_id=run_id,
            bid_id=bid_id,
            snapshot_id=snapshot_id,
            operation_id=operation_id,
            # The immutable legacy column cannot represent PARTIAL. Detailed readers use
            # run_outcomes.outcome; legacy status is FAILED only for a fully failed run.
            status=(
                RunStatus.FAILED
                if run_outcome.outcome is DiscoveryOutcome.FAILED
                else RunStatus.SUCCEEDED
            ),
            proposal_count=len(proposals),
            rejected_source_count=rejected_count,
            actor=actor,
            started_at=started_at,
            completed_at=completed_at,
        )
        self.repository.create_run(
            run,
            extractions,
            proposals,
            actor,
            run_outcome=run_outcome,
            file_outcomes=file_outcomes,
            collisions=collisions,
        )
        for existing_id, sources in appends:
            self.repository.append_citations(existing_id, list(sources))
        return self.repository.existing_run(bid_id, operation_id) or run.model_dump(mode="json")

    # -- review queue --------------------------------------------------------------------

    def list_review_queue(self, bid_id: str) -> list[dict[str, Any]]:
        rows = self.repository.list_proposals(bid_id)
        for row in rows:
            decision = self.repository.current_decision(row["proposal_id"])
            row["current_decision"] = decision["decision"] if decision else "PENDING"
            row["linked_deliverable_id"] = decision["linked_deliverable_id"] if decision else None
            row["unambiguous"] = row["criticality"] in ("REQUIRED",) or (
                row["criticality"] == "CONDITIONAL" and bool(row["condition_text"])
            )
            row["needs_criticality"] = False
            row["accept_goes_to"] = ""
            if row["classification"] is None:
                row["acceptable"] = True
                row["accept_block_reason"] = ""
                row["accept_goes_to"] = "Creates a deliverable due with the bid"
            else:
                classification = ProposalClassification.model_validate(row["classification"])
                # A reviewer can settle an unclear "how firmly required" while accepting, so it
                # does not block the button; every other missing choice does.
                acceptable, reason = is_acceptable(classification, row["condition_text"], True)
                row["acceptable"] = acceptable
                row["accept_block_reason"] = reason
                row["needs_criticality"] = acceptable and needs_criticality(
                    classification, row["condition_text"]
                )
                row["accept_goes_to"] = _accept_consequence(classification)
            row["collisions"] = []

        rows_by_id = {str(row["proposal_id"]): row for row in rows}
        for collision in self.repository.list_collisions(bid_id):
            reasons = json.loads(str(collision["reasons_json"]))
            for proposal_id, other_proposal_id in (
                (str(collision["proposal_id"]), str(collision["other_proposal_id"])),
                (str(collision["other_proposal_id"]), str(collision["proposal_id"])),
            ):
                row = rows_by_id.get(proposal_id)
                other = rows_by_id.get(other_proposal_id)
                if row is None or other is None:
                    continue
                primary_source = other["sources"][0]
                row["collisions"].append(
                    {
                        "title": other["title"],
                        "document": self._source_name(bid_id, other)
                        or primary_source.get("document_version_id")
                        or "Controlled document",
                        "locator": primary_source["locator"],
                        "reasons": reasons,
                        "current_decision": other["current_decision"],
                    }
                )
        return rows

    def decide(
        self, bid_id: str, decisions: list[ProposalDecisionInput], reviewer: str
    ) -> list[dict[str, Any]]:
        """Apply one or more review decisions atomically at the caller's boundary — each
        proposal's decision is independent, so a per-proposal failure does not roll back the
        others already applied in this call; the caller reports failures back to the reviewer.
        """
        results: list[dict[str, Any]] = []
        for decision_input in decisions:
            try:
                results.append(self._decide_one(bid_id, decision_input, reviewer))
            except Exception as exc:
                results.append({"proposal_id": decision_input.proposal_id, "error": str(exc)})
        return results

    def _decide_one(
        self, bid_id: str, decision_input: ProposalDecisionInput, reviewer: str
    ) -> dict[str, Any]:
        proposal = self.repository.get_proposal(decision_input.proposal_id)
        if proposal is None or proposal["bid_id"] != bid_id:
            raise ValueError("proposal not found in this Bid")
        current = self.repository.current_decision(decision_input.proposal_id)
        if current and current["decision"] == "ACCEPTED":
            raise ValueError("proposal already accepted")

        classification = _effective_classification(proposal)
        if decision_input.decision is ProposalReviewDecision.ACCEPTED and classification:
            return self._accept_classified(
                bid_id, proposal, classification, decision_input, reviewer, current
            )

        linked_deliverable_id: str | None = None
        item: Deliverable | None = None
        link: DeliverableLink | None = None
        if decision_input.decision is ProposalReviewDecision.ACCEPTED:
            item, link = self._build_deliverable(bid_id, proposal, decision_input, reviewer)
            linked_deliverable_id = item.deliverable_id

        event = ProposalReviewEvent(
            proposal_id=decision_input.proposal_id,
            bid_id=bid_id,
            decision=decision_input.decision,
            reviewer=reviewer,
            rationale=decision_input.rationale,
            linked_deliverable_id=linked_deliverable_id,
            supersedes_event_id=current["event_id"] if current else None,
            reviewed_at=datetime.now(UTC),
        )
        if item is not None and link is not None:
            self.repository.accept_proposal(item, link, event, reviewer)
        else:
            self.repository.add_review_event(event, reviewer)
        return {"proposal_id": decision_input.proposal_id, "deliverable_id": linked_deliverable_id}

    def _accept_classified(
        self,
        bid_id: str,
        proposal: dict[str, Any],
        classification: ProposalClassification,
        decision_input: ProposalDecisionInput,
        reviewer: str,
        current: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Accept a classified finding into the register its lifecycle names.

        Mirrors the legacy Deliverable path for DELIVERABLE destinations; the other registers are
        written by ``DestinationWriter`` and then recorded by ``accept_into_record``.
        """
        acceptable, reason = is_acceptable(
            classification, proposal["condition_text"], decision_input.criticality is not None
        )
        if not acceptable:
            raise ValueError(reason)
        kind = destination_for(classification)
        if kind is None or self.destinations is None:
            raise ValueError("This finding cannot be registered yet. Move it to a lifecycle first.")
        supersedes = current["event_id"] if current else None
        due_date, date_basis = self._accepted_due_date(proposal, decision_input, reviewer)
        source_name = self._source_name(bid_id, proposal)
        has_collision = any(
            proposal["proposal_id"] in (collision["proposal_id"], collision["other_proposal_id"])
            for collision in self.repository.list_collisions(bid_id)
        )

        if kind is DestinationKind.DELIVERABLE:
            phase = deliverable_phase(classification)
            existing_id = self.destinations.existing_deliverable(
                bid_id,
                proposal["title"],
                phase,
                reuse_by_title=not has_collision,
            )
            if existing_id is None:
                item, link = self._build_deliverable(
                    bid_id, proposal, decision_input, reviewer, classification
                )
                event = ProposalReviewEvent(
                    proposal_id=decision_input.proposal_id,
                    bid_id=bid_id,
                    decision=ProposalReviewDecision.ACCEPTED,
                    reviewer=reviewer,
                    linked_deliverable_id=item.deliverable_id,
                    destination_kind=DestinationKind.DELIVERABLE,
                    destination_record_id=item.deliverable_id,
                    supersedes_event_id=supersedes,
                    reviewed_at=datetime.now(UTC),
                )
                self.repository.accept_proposal(item, link, event, reviewer)
                return {
                    "proposal_id": decision_input.proposal_id,
                    "deliverable_id": item.deliverable_id,
                    "destination": kind.value,
                    "reused": False,
                }
            record = AcceptedRecord(
                kind, existing_id, True, "Already registered as a Deliverable of this phase"
            )
            source_link = self._source_link(bid_id, proposal, existing_id, reviewer)
        else:
            facts = _finding_facts(
                bid_id,
                proposal,
                classification,
                _source_label(proposal, source_name),
                due_date,
                date_basis,
            )
            record = self.destinations.register(
                kind,
                facts,
                reviewer,
                decision_input.package_id,
                reuse_by_title=not has_collision,
            )
            source_link = None
        event = ProposalReviewEvent(
            proposal_id=decision_input.proposal_id,
            bid_id=bid_id,
            decision=ProposalReviewDecision.ACCEPTED,
            reviewer=reviewer,
            linked_deliverable_id=record.record_id if kind is DestinationKind.DELIVERABLE else None,
            destination_kind=kind,
            destination_record_id=record.record_id,
            supersedes_event_id=supersedes,
            reviewed_at=datetime.now(UTC),
        )
        accept_into_record(
            self.repository._conn, self.repository._audit, event, record, source_link, reviewer
        )
        return {
            "proposal_id": decision_input.proposal_id,
            "deliverable_id": record.record_id if kind is DestinationKind.DELIVERABLE else None,
            "destination": kind.value,
            "record_id": record.record_id,
            "reused": record.reused,
        }

    def promote_source_to_controlled_version(
        self,
        bid_id: str,
        deliverable_id: str,
        citation_id: str,
        document_version_id: str,
        rationale: str,
        operation_id: str,
        actor: str,
    ) -> SourcePromotion:
        normalized_rationale = rationale.strip()
        if not normalized_rationale:
            raise ValueError("promotion rationale is required; explain why this version applies")
        return self.repository.promote_source(
            bid_id=bid_id,
            deliverable_id=deliverable_id,
            citation_id=citation_id,
            document_version_id=document_version_id,
            rationale=normalized_rationale,
            operation_id=operation_id,
            actor=actor,
        )

    def _accepted_due_date(
        self,
        proposal: dict[str, Any],
        decision_input: ProposalDecisionInput,
        reviewer: str,
    ) -> tuple[date | None, str]:
        """The due date an accepted finding may carry, and how it is known.

        Only a date the reviewer confirmed, or one stated in the document or calculated from a
        recorded trigger, becomes a fixed due date. A model-proposed date is described but not
        applied; it never silently becomes the customer's deadline.
        """
        if decision_input.due_date is not None:
            return decision_input.due_date, f"confirmed by {reviewer} against the source"
        if proposal.get("due_date"):
            effective = date.fromisoformat(str(proposal["due_date"]))
            if proposal.get("due_date_basis") == DueDateBasis.CALCULATED.value:
                rule = proposal.get("due_date_text") or "the customer's calendar-day rule"
                return effective, f"calculated from a recorded date by the rule “{rule}”"
            return effective, "stated in the customer's document"
        proposed = proposal.get("proposed_due_date")
        if proposed:
            rule = f" (from “{proposal['due_date_text']}”)" if proposal.get("due_date_text") else ""
            return None, f"not confirmed; analysis suggested {proposed}{rule}"
        return None, "not stated"

    def _source_name(self, bid_id: str, proposal: dict[str, Any]) -> str | None:
        """The file name behind a finding's primary source, when it is a received file."""
        file_id = proposal["sources"][0].get("received_file_id")
        if not file_id:
            return None
        try:
            return str(self.bid_package_repository.file(str(file_id), bid_id)["original_filename"])
        except Exception:
            return None

    def _build_deliverable(
        self,
        bid_id: str,
        proposal: dict[str, Any],
        decision_input: ProposalDecisionInput,
        reviewer: str,
        classification: ProposalClassification | None = None,
    ) -> tuple[Deliverable, DeliverableLink]:
        if classification is None:
            criticality = decision_input.criticality or _map_criticality(proposal["criticality"])
            phase = LifecyclePhase.WITH_BID
        else:
            criticality = decision_input.criticality or deliverable_criticality(classification)
            if criticality is DeliverableCriticality.CONDITIONAL and not proposal["condition_text"]:
                criticality = None
            phase = deliverable_phase(classification)
        if criticality is None:
            raise ValueError("an UNCLEAR proposal requires an explicit criticality to accept")
        due_date, date_basis = self._accepted_due_date(proposal, decision_input, reviewer)
        primary_source = proposal["sources"][0]
        source_id = primary_source["document_version_id"] or primary_source["received_file_id"]
        if not source_id:
            raise ValueError("proposal source identity is missing")

        now = datetime.now(UTC)
        item = Deliverable(
            bid_id=bid_id,
            title=proposal["title"],
            description=_description(proposal, self._source_name(bid_id, proposal), date_basis),
            category=proposal["category"],
            criticality=criticality,
            lifecycle_phase=phase,
            direction=DeliverableDirection.COMPANY_TO_CUSTOMER,
            owner=decision_input.owner or proposal["proposed_owner"],
            recipient=decision_input.recipient or proposal["requested_recipient"],
            due_basis=DueBasis.FIXED_DATE if due_date else DueBasis.UNSCHEDULED,
            fixed_due_date=due_date,
            condition_text=proposal["condition_text"],
            required_format=proposal["requested_format"],
            provenance=Provenance(
                created_by=Actor.HUMAN,
                agent_name="deliverable_discovery",
                model=getattr(self.llm_client, "model_id", None) or "local-llm",
                source_document_id=source_id,
                source_location=primary_source["locator"],
                human_confirmed=True,
                confirmed_by=reviewer,
                confirmed_at=now,
            ),
            created_at=now,
            updated_at=now,
            created_by=reviewer,
        )
        return item, self._source_link(bid_id, proposal, item.deliverable_id, reviewer)

    def _source_link(
        self, bid_id: str, proposal: dict[str, Any], deliverable_id: str, reviewer: str
    ) -> DeliverableLink:
        """The link from a Deliverable to the finding's primary source."""
        primary_source = proposal["sources"][0]
        source_id = primary_source["document_version_id"] or primary_source["received_file_id"]
        if not source_id:
            raise ValueError("proposal source identity is missing")
        is_received_file = primary_source["received_file_id"] is not None
        link = DeliverableLink(
            bid_id=bid_id,
            deliverable_id=deliverable_id,
            target_type=(
                DeliverableTargetType.RECEIVED_FILE
                if is_received_file
                else DeliverableTargetType.DOCUMENT_VERSION
            ),
            target_id=source_id,
            relation=(
                DeliverableRelation.SOURCE_RECEIVED_FILE
                if is_received_file
                else DeliverableRelation.SOURCE_DOCUMENT_VERSION
            ),
            created_at=datetime.now(UTC),
            created_by=reviewer,
        )
        return link

    # -- addendum integration -------------------------------------------------------------

    def detect_addendum_impacts(self, bid_id: str, actor: str) -> list[str]:
        """Scan every current addendum directive with a document target and flag any proposal
        or accepted deliverable sourced from that document version. Safe to call repeatedly —
        `flag_addendum_effect` only appends new flag rows and `add_addendum_flag` has no
        uniqueness constraint beyond the supersede chain, so callers should treat a directive
        already fully flagged as a no-op in practice (the review queue simply shows the same
        flag again, never a duplicate obligation)."""
        flagged: list[str] = []
        for directive in self.bid_package_repository.list_current_directives(bid_id):
            target = directive.get("target_document_version_id")
            if not target:
                continue
            rationale = (
                f"Addendum directive {directive['directive_id']} "
                f"({directive.get('directive_type', 'CHANGE')}) targets controlled document "
                f"version {target}."
            )
            flagged.extend(
                self.flag_addendum_effect(
                    bid_id, str(directive["directive_id"]), str(target), rationale, actor
                )
            )
        return flagged

    def flag_addendum_effect(
        self,
        bid_id: str,
        directive_id: str,
        document_version_id: str,
        rationale: str,
        actor: str,
    ) -> list[str]:
        """Flag any proposal or accepted deliverable sourced from `document_version_id` as
        potentially affected by this addendum directive. Appends only — never mutates the
        flagged record. Idempotent per (directive_id, target): re-running the same directive
        against the same proposal/deliverable does not append a second flag."""
        already_flagged = {
            (row["directive_id"], row.get("proposal_id"), row.get("deliverable_id"))
            for row in self.repository.list_addendum_flags(bid_id)
        }
        received_files = {
            str(row["file_id"]): row
            for row in self.repository.received_file_ids_for_version(bid_id, document_version_id)
        }

        def received_file_rationale(file_id: str) -> str:
            file_row = received_files[file_id]
            return (
                f"{rationale.rstrip()} The targeted controlled version "
                f"{document_version_id} is linked to received file {file_id} "
                f"({file_row['original_filename']}) as {file_row['relationship']}; "
                "this item cites that file and needs review."
            )

        flagged: list[str] = []
        for row in self.repository.list_proposals(bid_id):
            flag_key = (directive_id, row["proposal_id"], None)
            if flag_key in already_flagged:
                continue
            flag_rationale: str | None = None
            if any(s.get("document_version_id") == document_version_id for s in row["sources"]):
                flag_rationale = rationale
            else:
                matched_file_id = next(
                    (
                        str(source["received_file_id"])
                        for source in row["sources"]
                        if source.get("received_file_id") in received_files
                    ),
                    None,
                )
                if matched_file_id is not None:
                    flag_rationale = received_file_rationale(matched_file_id)
            if flag_rationale is not None:
                self.repository.add_addendum_flag(
                    AddendumDiscoveryFlag(
                        bid_id=bid_id,
                        directive_id=directive_id,
                        proposal_id=row["proposal_id"],
                        rationale=flag_rationale,
                        created_at=datetime.now(UTC),
                        created_by=actor,
                    )
                )
                flagged.append(row["proposal_id"])
                already_flagged.add(flag_key)
        for link_row in self.deliverable_repository.list(bid_id):
            deliverable_id = link_row["deliverable_id"]
            flag_key = (directive_id, None, deliverable_id)
            if flag_key in already_flagged:
                continue
            links = self.deliverable_repository.links(deliverable_id)
            flag_rationale = None
            matched_file_id = next(
                (
                    str(link["target_id"])
                    for link in links
                    if link["target_type"] == "RECEIVED_FILE"
                    and link["target_id"] in received_files
                ),
                None,
            )
            if matched_file_id is not None:
                # Most specific reason first: a promoted deliverable also carries a
                # DOCUMENT_VERSION link, but the received-file rationale names both.
                flag_rationale = received_file_rationale(matched_file_id)
            elif any(
                link["target_type"] == "DOCUMENT_VERSION"
                and link["target_id"] == document_version_id
                for link in links
            ):
                flag_rationale = rationale
            if flag_rationale is not None:
                self.repository.add_addendum_flag(
                    AddendumDiscoveryFlag(
                        bid_id=bid_id,
                        directive_id=directive_id,
                        deliverable_id=deliverable_id,
                        rationale=flag_rationale,
                        created_at=datetime.now(UTC),
                        created_by=actor,
                    )
                )
                flagged.append(deliverable_id)
                already_flagged.add(flag_key)
        return flagged

    # -- export / import (Work ChatGPT preparation) ----------------------------------------

    def export_package(self, bid_id: str) -> ExportPackage:
        proposals = self.repository.list_proposals(bid_id)
        latest_run, _ = self.bid_package_repository.latest_discovery_run_coverage(bid_id)
        return ExportPackage(
            schema_version=SCHEMA_VERSION,
            bid_id=bid_id,
            exported_at=datetime.now(UTC),
            discovery_outcome=(
                DiscoveryOutcome(str(latest_run["outcome"]))
                if latest_run is not None and latest_run.get("outcome") is not None
                else None
            ),
            proposals=tuple(
                ExportProposal(
                    title=row["title"],
                    category=row["category"],
                    criticality=DiscoveryCriticality(row["criticality"]),
                    condition_text=row["condition_text"],
                    due_date=date.fromisoformat(row["due_date"]) if row["due_date"] else None,
                    requested_format=row["requested_format"],
                    requested_recipient=row["requested_recipient"],
                    proposed_owner=row["proposed_owner"],
                    sources=tuple(
                        ExportSourceCitation(
                            document_version_id=s["document_version_id"],
                            received_file_id=s["received_file_id"],
                            locator_type=LocatorType(s["locator_type"]),
                            locator=s["locator"],
                            excerpt=s["excerpt"],
                        )
                        for s in row["sources"]
                    ),
                )
                for row in proposals
            ),
        )

    def import_package(
        self, bid_id: str, payload: str | bytes, operation_id: str, actor: str
    ) -> dict[str, Any]:
        existing = self.repository.existing_run(bid_id, operation_id)
        if existing is not None:
            return existing
        try:
            raw = json.loads(payload)
            package = ExportPackage.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ImportRejectedError(f"invalid import package: {exc}") from exc

        if package.schema_version != SCHEMA_VERSION:
            raise ImportRejectedError(
                f"unsupported schema_version {package.schema_version!r}, expected "
                f"{SCHEMA_VERSION!r}"
            )
        if package.bid_id != bid_id:
            raise ImportRejectedError(
                "import package bid_id does not match the Bid it is being imported into "
                "— cross-Bid findings cannot be imported"
            )

        started_at = datetime.now(UTC)
        run_id = f"DDN-{uuid4().hex}"
        accepted_proposals: list[DeliverableProposal] = []
        rejected = 0
        known_dedup_keys = self.repository.existing_dedup_keys(bid_id)
        for candidate in package.proposals:
            valid_sources = tuple(
                source.source_citation
                for source in candidate.sources
                if self.repository.known_source(
                    bid_id,
                    source.document_version_id,
                    source.received_file_id,
                    source.locator,
                )
            )
            if len(valid_sources) != len(candidate.sources) or not valid_sources:
                rejected += 1
                continue
            dedup_key = normalize_dedup_key(bid_id, candidate.title, candidate.category)
            if dedup_key in known_dedup_keys:
                # Already proposed in an earlier run/import — reimport/replay must never
                # create a duplicate authoritative item; the existing proposal is still there.
                continue
            known_dedup_keys.add(dedup_key)
            accepted_proposals.append(
                DeliverableProposal(
                    bid_id=bid_id,
                    run_id=run_id,
                    title=candidate.title,
                    category=candidate.category,
                    criticality=candidate.criticality,
                    condition_text=candidate.condition_text,
                    # An imported date is the importer's claim, not the document's.
                    due_date=None,
                    due_date_basis=(
                        DueDateBasis.PROPOSED if candidate.due_date else DueDateBasis.UNKNOWN
                    ),
                    proposed_due_date=candidate.due_date,
                    requested_format=candidate.requested_format,
                    requested_recipient=candidate.requested_recipient,
                    proposed_owner=candidate.proposed_owner,
                    sources=valid_sources,
                    dedup_key=dedup_key,
                    created_at=started_at,
                )
            )

        completed_at = datetime.now(UTC)
        run = DiscoveryRun(
            run_id=run_id,
            bid_id=bid_id,
            snapshot_id=None,
            operation_id=operation_id,
            status=RunStatus.SUCCEEDED,
            proposal_count=len(accepted_proposals),
            rejected_source_count=rejected,
            actor=actor,
            started_at=started_at,
            completed_at=completed_at,
        )
        self.repository.create_run(run, [], accepted_proposals, actor)
        return self.repository.existing_run(bid_id, operation_id) or run.model_dump(mode="json")


def _effective_classification(proposal: dict[str, Any]) -> ProposalClassification | None:
    data = proposal.get("classification")
    return ProposalClassification.model_validate(data) if data else None


def _source_label(proposal: dict[str, Any], source_name: str | None) -> str:
    primary = proposal["sources"][0]
    where = f"{primary['locator_type'].title()} {primary['locator']}"
    return f"{source_name or 'controlled document'} {where}"


def _finding_facts(
    bid_id: str,
    proposal: dict[str, Any],
    classification: ProposalClassification,
    source_label: str,
    due_date: date | None,
    date_basis: str,
) -> FindingFacts:
    primary = proposal["sources"][0]
    return FindingFacts(
        bid_id=bid_id,
        proposal_id=proposal["proposal_id"],
        title=proposal["title"],
        description=classification.description or proposal["title"],
        quote=primary.get("quote") or primary["excerpt"][:400],
        source_label=source_label,
        timing_text=proposal.get("due_date_text"),
        due_date=due_date,
        due_note=date_basis,
        condition_text=proposal["condition_text"],
        required=classification.requirement_status is not RequirementStatus.OPTIONAL,
        commercial_topic=classification.commercial_topic,
    )


def _map_criticality(value: str) -> DeliverableCriticality | None:
    return {
        "REQUIRED": DeliverableCriticality.MANDATORY,
        "CONDITIONAL": DeliverableCriticality.CONDITIONAL,
    }.get(value)


def _description(proposal: dict[str, Any], source_name: str | None, date_basis: str) -> str:
    primary = proposal["sources"][0]
    if source_name:
        source_label = source_name
    elif primary["received_file_id"] is not None:
        source_label = f"received file {primary['received_file_id']}"
    else:
        source_label = f"document version {primary['document_version_id']}"
    evidence = primary.get("quote") or primary["excerpt"][:400]
    summary = f"{proposal['description']}\n" if proposal.get("description") else ""
    return (
        f"{summary}Proposed from {primary['locator_type']} {primary['locator']} of {source_label}: "
        f"{evidence}\n{DUE_DATE_BASIS_MARKER} {date_basis}."
    )


_CONFIDENCE_RANK = {Confidence.HIGH: 2, Confidence.MEDIUM: 1, Confidence.LOW: 0}


def _candidate_rank(candidate: ProposedCandidate) -> int:
    """Among candidates naming one obligation, the most confident classification represents it."""
    if candidate.classification is None:
        return 0
    return _CONFIDENCE_RANK[candidate.classification.confidence]


_ACCEPT_CONSEQUENCE = {
    DestinationKind.VENDOR_DOCUMENT: "Adds a requirement to the manufacturer document register",
    DestinationKind.COMMERCIAL_POSITION: "Adds this as evidence on a commercial position",
    DestinationKind.WORK_ITEM: "Creates an action in My Work",
}


def _accept_consequence(classification: ProposalClassification) -> str:
    """What Accept will do, in plain words, shown beside the button."""
    kind = destination_for(classification)
    if kind is None:
        return ""
    if kind is DestinationKind.DELIVERABLE:
        phase = deliverable_phase(classification)
        return (
            "Creates a deliverable due after award"
            if phase is LifecyclePhase.POST_AWARD
            else "Creates a deliverable due with the bid"
        )
    return _ACCEPT_CONSEQUENCE[kind]


@dataclass(frozen=True)
class DuplicateSplit:
    """Cross-run merge result; iterable as the historical ``(new, appends)`` pair."""

    new: list[DeliverableProposal]
    appends: list[tuple[str, tuple[SourceCitation, ...]]]
    collisions: list[DiscoveryCollision]

    def __iter__(
        self,
    ) -> Iterator[list[DeliverableProposal] | list[tuple[str, tuple[SourceCitation, ...]]]]:
        yield self.new
        yield self.appends

    def __getitem__(
        self, index: int
    ) -> list[DeliverableProposal] | list[tuple[str, tuple[SourceCitation, ...]]]:
        return (self.new, self.appends)[index]


def split_known_duplicates(
    proposals: list[DeliverableProposal],
    known: Mapping[str, str],
    existing_by_base: Mapping[str, tuple[ExistingObligation, ...]] | None = None,
) -> DuplicateSplit:
    """Separate findings new to the Bid from re-findings of an existing finding.

    ``known`` maps dedup keys already recorded for the Bid to their proposal ids. A re-finding is
    not stored twice: its sources are returned to be appended to the existing finding, which
    keeps every document that supports the obligation. A bid-stage finding also matches one
    recorded before lifecycle classification, which used the title+category key.
    """
    new: list[DeliverableProposal] = []
    appends: list[tuple[str, tuple[SourceCitation, ...]]] = []
    collisions: list[DiscoveryCollision] = []
    for proposal in proposals:
        base_key = base_dedup_key(proposal.dedup_key)
        classification = proposal.classification
        lookup_keys = [base_key]
        if (
            classification is not None
            and classification.lifecycle is DiscoveryLifecycle.BID_RETURNABLE
        ):
            lookup_keys.append(
                normalize_dedup_key(proposal.bid_id, proposal.title, proposal.category)
            )

        stored = [item for key in lookup_keys for item in (existing_by_base or {}).get(key, ())]
        proposal_facts = facts_from_proposal(proposal)
        comparisons = [(item, compare_obligations(proposal_facts, item.facts)) for item in stored]
        equivalent = next((item for item, result in comparisons if result.equivalent), None)
        if equivalent is not None:
            appends.append((equivalent.proposal_id, proposal.sources))
            continue

        if stored:
            if proposal.dedup_key == base_key or proposal.dedup_key in known:
                proposal = proposal.model_copy(
                    update={"dedup_key": collision_dedup_key(base_key, proposal_facts)}
                )
            collisions.extend(
                DiscoveryCollision.canonical(
                    proposal.proposal_id, item.proposal_id, comparison.reasons
                )
                for item, comparison in comparisons
            )
            new.append(proposal)
            continue

        # Compatibility for callers that only have the historical key -> id index.  Production
        # discovery always supplies ``existing_by_base`` and therefore always compares facts.
        existing = next(
            (known[key] for key in [proposal.dedup_key, *lookup_keys] if key in known),
            None,
        )
        if existing is not None:
            appends.append((existing, proposal.sources))
        else:
            new.append(proposal)
    return DuplicateSplit(new, appends, collisions)


_BASIS_RANK = {
    DueDateBasis.STATED: 0,
    DueDateBasis.CALCULATED: 1,
    DueDateBasis.PROPOSED: 2,
    DueDateBasis.UNKNOWN: 3,
}


def _consolidate(
    bid_id: str,
    run_id: str,
    created_at: datetime,
    candidates: list[ProposedCandidate],
    extraction_lookup: dict[str, ExtractionUnit],
    triggers: Mapping[str, date] | None = None,
) -> list[DeliverableProposal]:
    """Merge only materially equivalent candidates sharing a title/lifecycle key.

    Each citation carries the sentence the model quoted, but only when that sentence is found word
    for word in the passage; its excerpt is then the text around it rather than the top of the
    page. Each due date is classified against the passage (see ``core.date_integrity``): only a
    date that is stated or calculated becomes ``due_date``; the model's own guess stays a proposal.
    A later non-equivalent candidate gets a source-fingerprint suffix so both findings can persist.
    """
    grouped: dict[str, list[ProposedCandidate]] = {}
    for candidate in candidates:
        key = normalize_dedup_key(
            bid_id, candidate.title, candidate.category, candidate.classification
        )
        grouped.setdefault(key, []).append(candidate)

    def build_proposal(
        dedup_key: str, group: list[ProposedCandidate]
    ) -> DeliverableProposal | None:
        first = max(group, key=_candidate_rank)
        sources: list[SourceCitation] = []
        dates = []
        cited: set[tuple[str, str, str]] = set()
        for candidate in group:
            unit = extraction_lookup.get(candidate.extraction_id)
            if unit is None:
                continue
            passage = unit.text_excerpt
            span = locate_quote(passage, candidate.source_quote)
            # One citation per (file, locator), the rule ``append_citations`` applies across runs.
            source_id = str(unit.document_version_id or unit.received_file_id)
            citation_key = (source_id, unit.locator_type.value, unit.locator)
            if citation_key not in cited:
                cited.add(citation_key)
                sources.append(
                    SourceCitation(
                        document_version_id=unit.document_version_id,
                        received_file_id=unit.received_file_id,
                        locator_type=unit.locator_type,
                        locator=unit.locator,
                        excerpt=evidence_window(passage, span) if span else passage[:2000],
                        quote=passage[span[0] : span[1]] if span else None,
                    )
                )
            dates.append(
                classify_due_date(
                    passage,
                    parse_date(candidate.due_date),
                    candidate.due_date_text,
                    triggers,
                )
            )
        if not sources:
            return None
        due = min(dates, key=lambda item: _BASIS_RANK[item.basis])
        return DeliverableProposal(
            bid_id=bid_id,
            run_id=run_id,
            title=first.title,
            category=first.category,
            criticality=first.criticality,
            condition_text=first.condition_text,
            due_date=due.effective,
            due_date_basis=due.basis,
            proposed_due_date=due.proposed,
            due_date_text=next((item.rule_text for item in dates if item.rule_text), None),
            requested_format=next((c.requested_format for c in group if c.requested_format), None),
            requested_recipient=next(
                (c.requested_recipient for c in group if c.requested_recipient), None
            ),
            proposed_owner=next((c.proposed_owner for c in group if c.proposed_owner), None),
            classification=first.classification,
            sources=tuple(sources),
            dedup_key=dedup_key,
            created_at=created_at,
        )

    proposals: list[DeliverableProposal] = []
    for dedup_key, group in grouped.items():
        clusters: list[list[ProposedCandidate]] = []
        cluster_proposals: list[DeliverableProposal] = []
        for candidate in group:
            candidate_proposal = build_proposal(dedup_key, [candidate])
            if candidate_proposal is None:
                continue
            match_index = next(
                (
                    index
                    for index, existing in enumerate(cluster_proposals)
                    if compare_obligations(
                        facts_from_proposal(existing), facts_from_proposal(candidate_proposal)
                    ).equivalent
                ),
                None,
            )
            if match_index is None:
                clusters.append([candidate])
                cluster_proposals.append(candidate_proposal)
            else:
                clusters[match_index].append(candidate)
                rebuilt = build_proposal(dedup_key, clusters[match_index])
                if rebuilt is not None:
                    cluster_proposals[match_index] = rebuilt

        for index, proposal in enumerate(cluster_proposals):
            if index:
                proposal = proposal.model_copy(
                    update={
                        "dedup_key": collision_dedup_key(dedup_key, facts_from_proposal(proposal))
                    }
                )
            proposals.append(proposal)
    return proposals
