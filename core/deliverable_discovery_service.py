"""OPS-13A application service — orchestrates discovery, review and confirmation.

Confirmed proposals become real `Deliverable` rows through the existing create -> add_link ->
activate repository sequence, committed atomically with the acceptance review event. Rejected
and deferred proposals only ever gain a review event; they never become an obligation and never
touch readiness.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from core.bid_package_repository import BidPackageRepository
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
                            raw_units = extract_units(
                                str(evidence["original_filename"]),
                                str(evidence["detected_media_type"])
                                if evidence["detected_media_type"] is not None
                                else None,
                                io.BytesIO(data),
                            )
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
        proposals = _consolidate(bid_id, run_id, started_at, candidates, extraction_lookup)
        known_dedup_keys = self.repository.existing_dedup_keys(bid_id)
        proposals = [p for p in proposals if p.dedup_key not in known_dedup_keys]

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
        )
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

    def _build_deliverable(
        self,
        bid_id: str,
        proposal: dict[str, Any],
        decision_input: ProposalDecisionInput,
        reviewer: str,
    ) -> tuple[Deliverable, DeliverableLink]:
        criticality = decision_input.criticality or _map_criticality(proposal["criticality"])
        if criticality is None:
            raise ValueError("an UNCLEAR proposal requires an explicit criticality to accept")
        due_date = date.fromisoformat(proposal["due_date"]) if proposal["due_date"] else None
        primary_source = proposal["sources"][0]
        source_id = primary_source["document_version_id"] or primary_source["received_file_id"]
        if not source_id:
            raise ValueError("proposal source identity is missing")

        now = datetime.now(UTC)
        item = Deliverable(
            bid_id=bid_id,
            title=proposal["title"],
            description=_description(proposal),
            category=proposal["category"],
            criticality=criticality,
            lifecycle_phase=LifecyclePhase.WITH_BID,
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
        is_received_file = primary_source["received_file_id"] is not None
        link = DeliverableLink(
            bid_id=bid_id,
            deliverable_id=item.deliverable_id,
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
            created_at=now,
            created_by=reviewer,
        )
        return item, link

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
                    due_date=candidate.due_date,
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


def _map_criticality(value: str) -> DeliverableCriticality | None:
    return {
        "REQUIRED": DeliverableCriticality.MANDATORY,
        "CONDITIONAL": DeliverableCriticality.CONDITIONAL,
    }.get(value)


def _description(proposal: dict[str, Any]) -> str:
    primary = proposal["sources"][0]
    if primary["received_file_id"] is not None:
        source_label = f"received file {primary['received_file_id']}"
    else:
        source_label = f"document version {primary['document_version_id']}"
    return (
        f"Proposed from {primary['locator_type']} {primary['locator']} "
        f"of {source_label}: {primary['excerpt'][:400]}"
    )


def _consolidate(
    bid_id: str,
    run_id: str,
    created_at: datetime,
    candidates: list[ProposedCandidate],
    extraction_lookup: dict[str, ExtractionUnit],
) -> list[DeliverableProposal]:
    """Merge candidates that name the same obligation (same normalized title+category) into one
    proposal with multiple source citations, instead of one proposal per mention."""
    grouped: dict[str, list[ProposedCandidate]] = {}
    for candidate in candidates:
        key = normalize_dedup_key(bid_id, candidate.title, candidate.category)
        grouped.setdefault(key, []).append(candidate)

    proposals: list[DeliverableProposal] = []
    for dedup_key, group in grouped.items():
        first = group[0]
        sources = []
        for candidate in group:
            unit = extraction_lookup.get(candidate.extraction_id)
            if unit is None:
                continue
            sources.append(
                SourceCitation(
                    document_version_id=unit.document_version_id,
                    received_file_id=unit.received_file_id,
                    locator_type=unit.locator_type,
                    locator=unit.locator,
                    excerpt=unit.text_excerpt[:2000],
                )
            )
        if not sources:
            continue
        due_date = next(
            (date.fromisoformat(c.due_date) for c in group if c.due_date),
            None,
        )
        proposals.append(
            DeliverableProposal(
                bid_id=bid_id,
                run_id=run_id,
                title=first.title,
                category=first.category,
                criticality=first.criticality,
                condition_text=first.condition_text,
                due_date=due_date,
                requested_format=next(
                    (c.requested_format for c in group if c.requested_format), None
                ),
                requested_recipient=next(
                    (c.requested_recipient for c in group if c.requested_recipient), None
                ),
                proposed_owner=next((c.proposed_owner for c in group if c.proposed_owner), None),
                sources=tuple(sources),
                dedup_key=dedup_key,
                created_at=created_at,
            )
        )
    return proposals
