"""Stage 2 — send an accepted, classified finding to the register that owns it.

``core.requirement_lifecycle.destination_for`` decides the register; this module does the writing.
A Deliverable is created (or linked) by the discovery repository's own atomic transaction. The
other registers (vendor documents, commercial positions, work items) have their own repositories,
each atomic with its own audit rows, so a finding is registered in two steps:

1. find a record this finding already created (its origin marker ``[DISC:<proposal_id>]`` is stored
   in the record) or an equivalent one, otherwise create it;
2. one discovery transaction writes the ACCEPTED review event, the destination row and the
   audit row.

A failure between the steps leaves a record carrying the origin marker; accepting again finds it and
links to it, so a retry never creates a second record.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from core.deliverable_discovery import ProposalReviewDecision, ProposalReviewEvent
from core.deliverable_discovery_classification import AuditWriter, record_destination
from core.deliverable_discovery_origins import active_origin
from core.deliverable_discovery_reconcile import marker_in_commercial_history
from core.deliverable_repository import DeliverableRepository
from core.deliverables import DeliverableLink, LifecyclePhase
from core.ops08 import Ops08Repository, PositionRevision
from core.requirement_lifecycle import DestinationKind
from core.vendor_document_control import CustomerRequirementCreate, SupplierPackageCreate
from core.vendor_document_service import VendorDocumentService
from core.work_item_service import WorkItemService
from core.work_items import WorkCategory, WorkItemCreate

DEFAULT_PACKAGE_CODE = "RFP-MFR-INPUTS"
_NORMALIZE = re.compile(r"[^a-z0-9]+")


def origin_marker(proposal_id: str) -> str:
    """The text stored in a register record that links it to the finding that created it."""
    return f"[DISC:{proposal_id}]"


def normalized_title(title: str) -> str:
    """Case, spacing and punctuation-insensitive title used to recognise a duplicate."""
    return _NORMALIZE.sub("-", title.lower()).strip("-")


@dataclass(frozen=True)
class FindingFacts:
    """What a register record needs to say about the finding that created it."""

    bid_id: str
    proposal_id: str
    title: str
    description: str
    quote: str
    source_label: str
    timing_text: str | None
    due_date: date | None
    due_note: str
    condition_text: str | None
    required: bool
    commercial_topic: str | None


@dataclass(frozen=True)
class AcceptedRecord:
    """The register record a finding became, or was linked to."""

    kind: DestinationKind
    record_id: str
    reused: bool
    note: str
    detail: str | None = None


class DestinationWriter:
    """Create or find the register record for an accepted finding."""

    def __init__(
        self,
        deliverable_repository: DeliverableRepository,
        vendor_service: VendorDocumentService,
        ops08_repository: Ops08Repository,
        work_item_service: WorkItemService,
    ) -> None:
        self.deliverables = deliverable_repository
        self.vendor = vendor_service
        self.ops08 = ops08_repository
        self.work_items = work_item_service

    def existing_deliverable(
        self,
        bid_id: str,
        title: str,
        phase: LifecyclePhase,
        *,
        reuse_by_title: bool = True,
    ) -> str | None:
        """An open Deliverable with the same title and phase, so a re-finding links to it."""
        if not reuse_by_title:
            return None
        wanted = normalized_title(title)
        for row in self.deliverables.list(bid_id):
            if (
                row["workflow_state"] != "CANCELLED"
                and row["lifecycle_phase"] == phase.value
                and normalized_title(str(row["title"])) == wanted
            ):
                return str(row["deliverable_id"])
        return None

    def register(
        self,
        kind: DestinationKind,
        facts: FindingFacts,
        reviewer: str,
        package_id: str | None = None,
        *,
        reuse_by_title: bool = True,
    ) -> AcceptedRecord:
        """Create (or find) the vendor-document, commercial-position or work-item record."""
        if kind is DestinationKind.VENDOR_DOCUMENT:
            return self._vendor_requirement(facts, reviewer, package_id, reuse_by_title)
        if kind is DestinationKind.COMMERCIAL_POSITION:
            return self._commercial_position(facts, reviewer)
        if kind is DestinationKind.WORK_ITEM:
            return self._work_item(facts, reviewer, reuse_by_title)
        raise ValueError(f"{kind.value} records are created by the Deliverable acceptance path")

    # -- vendor-document register ---------------------------------------------------------

    def _vendor_requirement(
        self,
        facts: FindingFacts,
        reviewer: str,
        package_id: str | None,
        reuse_by_title: bool,
    ) -> AcceptedRecord:
        repository = self.vendor.repository
        packages = repository.list_packages(facts.bid_id)
        marker = origin_marker(facts.proposal_id)
        wanted = normalized_title(facts.title)
        for package in packages:
            for requirement in repository.list_requirements(package.package_id):
                if marker in (requirement.source_row_reference or ""):
                    return AcceptedRecord(
                        DestinationKind.VENDOR_DOCUMENT,
                        requirement.requirement_id,
                        False,
                        f"Manufacturer requirement in package {package.package_code}",
                    )
        if package_id:
            chosen = next((p for p in packages if p.package_id == package_id), None)
            if chosen is None:
                raise ValueError(
                    "That manufacturer package does not belong to this Bid. Choose one of this "
                    "Bid's packages."
                )
        elif len(packages) == 1:
            chosen = packages[0]
        elif not packages:
            chosen = self.vendor.create_package(
                SupplierPackageCreate(
                    bid_id=facts.bid_id,
                    package_name="RFP manufacturer inputs",
                    package_code=DEFAULT_PACKAGE_CODE,
                    proposed_manufacturer="To be confirmed",
                    internal_owner=reviewer,
                    notes="Created by RFP discovery to hold items to obtain from a manufacturer.",
                ),
                reviewer,
            )
        else:
            raise ValueError(
                "This Bid has several manufacturer packages. Choose a manufacturer package for "
                "this requirement, then accept it again."
            )
        if reuse_by_title:
            for requirement in repository.list_requirements(chosen.package_id):
                if normalized_title(requirement.deliverable_title) == wanted:
                    return AcceptedRecord(
                        DestinationKind.VENDOR_DOCUMENT,
                        requirement.requirement_id,
                        True,
                        f"Already in package {chosen.package_code}",
                    )
        reference = f"{facts.source_label} {origin_marker(facts.proposal_id)}"
        created = self.vendor.create_requirement(
            CustomerRequirementCreate(
                package_id=chosen.package_id,
                customer_requirement_code=f"DISC-{facts.proposal_id[-10:]}",
                deliverable_title=facts.title[:500],
                description=facts.description[:5000],
                required=facts.required,
                original_contractual_timing=(facts.timing_text or None),
                customer_notes=facts.quote[:5000] or None,
                source_row_reference=reference[-500:],
            ),
            reviewer,
        )
        return AcceptedRecord(
            DestinationKind.VENDOR_DOCUMENT,
            created.requirement_id,
            False,
            f"Manufacturer requirement in package {chosen.package_code}",
        )

    # -- commercial positions -------------------------------------------------------------

    def _commercial_position(self, facts: FindingFacts, reviewer: str) -> AcceptedRecord:
        topic = facts.commercial_topic or "QUALIFICATIONS"
        position_id = self.ops08.ensure_position(facts.bid_id, topic, reviewer, reviewer)
        detail = self.ops08.detail(position_id)
        marker = origin_marker(facts.proposal_id)
        existing = str(detail.get("supporting_evidence") or "")
        with self.ops08._conn() as conn:
            in_history = marker_in_commercial_history(conn, position_id, marker)
        if marker in existing or in_history:
            return AcceptedRecord(
                DestinationKind.COMMERCIAL_POSITION,
                position_id,
                False,
                f"Recorded as evidence on the {topic} commercial position",
            )
        line = f"RFP discovery {marker}: {facts.title} — “{facts.quote}” ({facts.source_label})"
        revision = self._current_revision(detail).model_copy(
            update={"supporting_evidence": f"{existing}\n{line}".strip()}
        )
        self.ops08.revise(position_id, int(detail["current_version"]), revision, reviewer)
        return AcceptedRecord(
            DestinationKind.COMMERCIAL_POSITION,
            position_id,
            False,
            f"Recorded as evidence on the {topic} commercial position",
            f"position version {int(detail['current_version']) + 1}",
        )

    @staticmethod
    def _current_revision(detail: dict[str, Any]) -> PositionRevision:
        """The position's current revision, every field carried forward unchanged."""
        values = {
            name: detail[name]
            for name in PositionRevision.model_fields
            if detail.get(name) is not None
        }
        return PositionRevision.model_validate(values)

    # -- work items -----------------------------------------------------------------------

    def _work_item(
        self, facts: FindingFacts, reviewer: str, reuse_by_title: bool
    ) -> AcceptedRecord:
        marker = origin_marker(facts.proposal_id)
        wanted = normalized_title(facts.title)
        # The origin marker is searched in every state: a work item completed or cancelled after
        # a split-write failure is still the record this finding created.
        for item in self.work_items.repository.list(facts.bid_id, active_only=False):
            if marker in (item.details or ""):
                return AcceptedRecord(
                    DestinationKind.WORK_ITEM, item.work_item_id, False, "Work item"
                )
        if reuse_by_title:
            for item in self.work_items.repository.list(facts.bid_id, active_only=True):
                if normalized_title(item.title) == wanted:
                    return AcceptedRecord(
                        DestinationKind.WORK_ITEM,
                        item.work_item_id,
                        True,
                        "Already an open work item",
                    )
        details = "\n".join(
            part
            for part in (
                facts.description,
                f"“{facts.quote}”" if facts.quote else "",
                f"Source: {facts.source_label}",
                f"Timing in the document: {facts.timing_text}" if facts.timing_text else "",
                f"Due date: {facts.due_note}.",
                f"Found by RFP discovery {marker}",
            )
            if part
        )
        created = self.work_items.create_work_item(
            WorkItemCreate(
                bid_id=facts.bid_id,
                title=facts.title[:300],
                details=details[:10_000],
                category=WorkCategory.CUSTOMER_REQUEST,
                due_date=facts.due_date,
            ),
            reviewer,
        )
        return AcceptedRecord(DestinationKind.WORK_ITEM, created.work_item_id, False, "Work item")


def accept_into_record(
    conn_factory: Callable[[], sqlite3.Connection],
    audit: AuditWriter,
    event: ProposalReviewEvent,
    record: AcceptedRecord,
    source_link: DeliverableLink | None,
    actor: str,
) -> None:
    """Write the ACCEPTED event, its destination row and audit in one transaction.

    Mirrors ``DeliverableDiscoveryRepository.accept_proposal`` (same stale-decision checks and event
    insert) for findings whose register record already exists. ``source_link`` adds the finding's
    source to an existing Deliverable that the finding was linked to.
    """
    if event.decision is not ProposalReviewDecision.ACCEPTED:
        raise ValueError("Only an accepted decision is recorded with a destination")
    with conn_factory() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            proposal = conn.execute(
                "SELECT bid_id FROM deliverable_discovery_proposals WHERE proposal_id=?",
                (event.proposal_id,),
            ).fetchone()
            if proposal is None or proposal["bid_id"] != event.bid_id:
                raise ValueError("proposal not found in this Bid")
            current = conn.execute(
                """SELECT base.event_id,base.decision
                FROM deliverable_discovery_review_events base
                WHERE base.proposal_id=? AND NOT EXISTS(
                    SELECT 1 FROM deliverable_discovery_review_events newer
                    WHERE newer.supersedes_event_id=base.event_id)
                ORDER BY base.reviewed_at DESC LIMIT 1""",
                (event.proposal_id,),
            ).fetchone()
            if current is not None and current["decision"] == "ACCEPTED":
                raise ValueError("proposal already accepted")
            _check_origin(conn, event.proposal_id, record)
            if event.supersedes_event_id != (str(current["event_id"]) if current else None):
                raise ValueError("review event supersedes stale current decision")
            conn.execute(
                "INSERT INTO deliverable_discovery_review_events VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    event.event_id,
                    event.proposal_id,
                    event.bid_id,
                    event.decision.value,
                    event.reviewer,
                    event.rationale,
                    event.linked_deliverable_id,
                    event.supersedes_event_id,
                    event.reviewed_at.isoformat(),
                ),
            )
            if source_link is not None:
                _link_source(conn, source_link)
            record_destination(
                conn,
                proposal_id=event.proposal_id,
                event_id=event.event_id,
                bid_id=event.bid_id,
                destination_kind=record.kind,
                record_id=record.record_id,
                reused=record.reused,
                note=record.note,
            )
            audit(
                conn,
                event.bid_id,
                actor,
                "deliverable_discovery_proposal_reviewed",
                f"{event.proposal_id}:{event.decision.value}:{record.kind.value}:{record.record_id}",
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _check_origin(conn: sqlite3.Connection, proposal_id: str, record: AcceptedRecord) -> None:
    """A record this finding created must have its origin; a reused record must not."""
    origin = active_origin(conn, proposal_id)
    if record.reused:
        if origin is not None:
            raise ValueError(
                "This finding already created a record of its own. Run the discovery "
                "reconciliation before accepting it into an existing record."
            )
        return
    if (
        origin is None
        or origin.record_id != record.record_id
        or origin.destination_kind != record.kind
    ):
        raise ValueError(
            "The record for this finding has no recorded origin. Accept the finding again; if "
            "the problem repeats, run the discovery reconciliation."
        )


def _link_source(conn: sqlite3.Connection, link: DeliverableLink) -> None:
    deliverable = conn.execute(
        "SELECT bid_id FROM deliverable_items WHERE deliverable_id=?", (link.deliverable_id,)
    ).fetchone()
    if deliverable is None or deliverable["bid_id"] != link.bid_id:
        raise ValueError("The Deliverable to link is missing or belongs to another Bid")
    exists = conn.execute(
        """SELECT 1 FROM deliverable_links WHERE deliverable_id=? AND target_type=? AND target_id=?
        AND relation=?""",
        (link.deliverable_id, link.target_type.value, link.target_id, link.relation.value),
    ).fetchone()
    if exists is None:
        conn.execute(
            "INSERT INTO deliverable_links VALUES (?,?,?,?,?,?,?,?)",
            (
                link.link_id,
                link.bid_id,
                link.deliverable_id,
                link.target_type.value,
                link.target_id,
                link.relation.value,
                link.created_at.isoformat(),
                link.created_by,
            ),
        )
