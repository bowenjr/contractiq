"""Typed presentation projections for the role-aligned bid control centre."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from enum import StrEnum
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.bid_repository import BidRepository
from core.bid_workflow import (
    PACKAGE_NOT_IMPORTED_HEADING,
    BidWorkflowProjection,
    OutstandingItem,
    PackageIntakeFacts,
    StageStatus,
    blocker_heading,
    intake_control_action,
    intake_heading,
)
from core.enums import BidLevel, BidStatus, Gate
from core.my_day import MyDayProjection, ProjectedRequirementAttention, work_item_attention_reasons
from core.owner_decision_evidence import initial_requirements_assessment_available
from core.owner_decisions import OwnerDecisionEvidenceSnapshot
from core.readiness import Blocker, ReadinessReport, ReadinessVerdict
from core.schemas import AuditEntry, Bid
from core.work_item_repository import WorkItemRepository
from core.work_items import WorkItem, WorkItemStatus


class BidPortfolioView(StrEnum):
    CURRENT = "current"
    HISTORY = "history"
    ALL = "all"
    UPCOMING = "upcoming"
    DORMANT = "dormant"
    ISSUED = "issued"


class BidDeadlineAttention(StrEnum):
    ANY = "any"
    OVERDUE = "overdue"
    UPCOMING = "upcoming"
    NONE = "none"


class BidReadinessFilter(StrEnum):
    ANY = "any"
    CLEAR = "clear"
    HOLD = "hold"


class BidWorkspaceSection(StrEnum):
    OVERVIEW = "overview"
    PACKAGE_INTAKE = "package-intake-addenda"
    REQUIREMENTS_SCOPE = "requirements-scope"
    MANUFACTURERS_COVERAGE = "manufacturers-coverage"
    COMMERCIAL_CONTRACT = "commercial-contract"
    PROPOSAL_NEGOTIATION = "proposal-negotiation"
    AWARD_HANDOVER = "award-handover"


class MyDayBidBucket(StrEnum):
    """One deterministic My Day grouping per active Bid — see `_my_day_bucket`."""

    REQUIRES_ACTION = "requires_action"
    WAITING = "waiting"
    ON_TRACK_OR_UPCOMING = "on_track_or_upcoming"
    HISTORY = "history"


DORMANT_AFTER_DAYS = 14
"""Presentation policy only: no recorded activity for this many days is "dormant".

This is an explicit UI threshold, not an inherent business fact, and is unrelated to the
`upcoming_horizon_days` deadline-attention window even though both currently default to the
same number of days.
"""


class BidPortfolioFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: BidPortfolioView = BidPortfolioView.CURRENT
    status: BidStatus | None = None
    classification: BidLevel | None = None
    readiness: BidReadinessFilter = BidReadinessFilter.ANY
    owner: str | None = Field(default=None, max_length=300)
    deadline: BidDeadlineAttention = BidDeadlineAttention.ANY

    @model_validator(mode="after")
    def validate_view_status(self) -> BidPortfolioFilters:
        if self.status is None or self.view is BidPortfolioView.ALL:
            return self
        if self.view is BidPortfolioView.CURRENT and self.status in HISTORY_BID_STATUSES:
            raise ValueError("Current view cannot be combined with a history-only Bid status")
        if self.view is BidPortfolioView.HISTORY and self.status in CURRENT_BID_STATUSES:
            raise ValueError("History view cannot be combined with a current Bid status")
        if self.view is BidPortfolioView.ISSUED and self.status is not BidStatus.SUBMITTED:
            raise ValueError("Issued view can only be combined with the Submitted Bid status")
        if self.view in {
            BidPortfolioView.UPCOMING,
            BidPortfolioView.DORMANT,
        } and self.status not in {BidStatus.ACTIVE, BidStatus.HELD}:
            raise ValueError(
                f"{self.view.value.title()} view can only be combined with "
                "the Active or Held Bid status"
            )
        return self


class ClassificationControl(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gate: Gate
    label: str
    required: bool
    explanation: str


class GovernanceLevelGuide(BaseModel):
    """Business-language presentation derived from the authoritative gate thresholds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    level: BidLevel
    name: str
    meaning: str
    controls: tuple[str, ...]
    adjacent_difference: str
    next_action: str


class BidBlockerView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    condition_id: str
    gate: Gate
    heading: str
    description: str
    detail: str
    consequence: str
    owner: str
    owing_party: str
    due_date: date | None
    evidence_source: str
    destination: str


class BidEvidenceLink(BaseModel):
    """One destination shown for a Bid only when its underlying signal is actually present."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    href: str


class BidPortfolioRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bid: Bid
    readiness: ReadinessReport
    development_gate_label: str = ""
    not_yet_assessed: tuple[str, ...] = ()
    pending_before_issue_count: int = 0
    highest_blocker: BidBlockerView | None
    additional_blocker_count: int = 0
    waiting_count: int
    overdue_work_count: int = 0
    decision_count: int
    other_attention_count: int = 0
    supplier_response_position: str = "Clear"
    last_activity: datetime | None = None
    next_action: str
    next_action_destination: str
    secondary_action: str | None = None
    secondary_action_destination: str | None = None
    stopped_reason: str | None = None
    deadline_attention: BidDeadlineAttention
    sort_tier: int = Field(ge=0, le=6)
    my_day_bucket: MyDayBidBucket = MyDayBidBucket.HISTORY
    evidence_links: list[BidEvidenceLink] = Field(default_factory=list)


class BidPortfolioProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date
    filters: BidPortfolioFilters
    rows: list[BidPortfolioRow]


class BidWorkspaceProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date
    bid: Bid
    readiness: ReadinessReport
    development_gate_label: str = ""
    not_yet_assessed: tuple[str, ...] = ()
    pending_before_issue_count: int = 0
    blockers: list[BidBlockerView]
    issue_blockers: list[BidBlockerView]
    waiting_items: list[WorkItem]
    waiting_control_attention: list[dict[str, str]]
    decision_attention: list[dict[str, str]]
    classification_controls: list[ClassificationControl]
    current_gate_label: str
    current_gate_description: str
    next_gate: Gate | None
    next_gate_label: str | None
    next_action: str
    next_action_destination: str
    workflow: BidWorkflowProjection | None = None


class BidWorkspaceAttention(BaseModel):
    """Bid-scoped attention loaded without projecting unrelated Bids."""

    model_config = ConfigDict(extra="forbid")

    approval_attention: list[dict[str, str]] = Field(default_factory=list)
    supplier_attention: list[dict[str, str]] = Field(default_factory=list)


class BidNotFoundError(ValueError):
    """Raised when a requested bid workspace does not exist."""


CURRENT_BID_STATUSES = frozenset({BidStatus.ACTIVE, BidStatus.HELD, BidStatus.SUBMITTED})
HISTORY_BID_STATUSES = frozenset({BidStatus.WON, BidStatus.LOST, BidStatus.NO_BID})

GATE_LABELS: dict[Gate, str] = {
    Gate.G0: "Bid setup",
    Gate.G1: "Bid / no-bid decision",
    Gate.G2: "Requirements, strategy and scope",
    Gate.G3: "Manufacturer and supplier coverage",
    Gate.G4: "Commercial and contract review",
    Gate.G5: "Proposal review",
    Gate.G6: "Submission authorization",
    Gate.G7: "Award and handover",
}

GATE_DESCRIPTIONS: dict[Gate, str] = {
    Gate.G0: "Confirm the Bid record, dates, ownership and classification.",
    Gate.G1: "Confirm the documented decision to pursue the Bid.",
    Gate.G2: "Resolve customer requirements, scope boundaries and Bid strategy.",
    Gate.G3: "Confirm manufacturer and supplier commitments; silence is unresolved.",
    Gate.G4: "Resolve commercial completeness, approvals and contract exposure.",
    Gate.G5: "Complete the controlled proposal review before submission.",
    Gate.G6: "Confirm the approved submission baseline and authority to submit.",
    Gate.G7: "Reconcile the award and transfer the accepted Bid baseline to execution.",
}

GATE_SECTION: dict[Gate, BidWorkspaceSection] = {
    Gate.G0: BidWorkspaceSection.OVERVIEW,
    Gate.G1: BidWorkspaceSection.OVERVIEW,
    Gate.G2: BidWorkspaceSection.REQUIREMENTS_SCOPE,
    Gate.G3: BidWorkspaceSection.MANUFACTURERS_COVERAGE,
    Gate.G4: BidWorkspaceSection.COMMERCIAL_CONTRACT,
    Gate.G5: BidWorkspaceSection.PROPOSAL_NEGOTIATION,
    Gate.G6: BidWorkspaceSection.PROPOSAL_NEGOTIATION,
    Gate.G7: BidWorkspaceSection.AWARD_HANDOVER,
}

_GATE_CONSEQUENCE: dict[Gate, str] = {
    Gate.G0: "The Bid cannot enter controlled qualification.",
    Gate.G1: "Effort may continue without an authorized pursuit decision.",
    Gate.G2: "The proposed scope or customer compliance position may be incomplete.",
    Gate.G3: "The Bid may rely on unsupported manufacturer commitments.",
    Gate.G4: "Price, terms or required approvals may be incomplete.",
    Gate.G5: "The proposal may not be ready for controlled review.",
    Gate.G6: "The Bid is not ready for authorized submission.",
    Gate.G7: "The awarded baseline is not ready for accepted handover.",
}

_GATE_OWING_PARTY: dict[Gate, str] = {
    Gate.G0: "Bid owner",
    Gate.G1: "Decision authority",
    Gate.G2: "Bid team",
    Gate.G3: "Manufacturer or supplier",
    Gate.G4: "Commercial, contract or approval owner",
    Gate.G5: "Proposal contributors and reviewers",
    Gate.G6: "Submission authority",
    Gate.G7: "Bid owner and execution recipient",
}


def workspace_path(bid_id: str, section: BidWorkspaceSection) -> str:
    """Return the stable browser destination for one Bid section."""
    if section is BidWorkspaceSection.OVERVIEW:
        return f"/bids/{bid_id}"
    return f"/bids/{bid_id}/{section.value}"


def gate_destination(bid_id: str, gate: Gate) -> str:
    return workspace_path(bid_id, GATE_SECTION[gate])


def blocker_destination(bid_id: str, condition_id: str, gate: Gate) -> str:
    """Return a direct resolution path for a gate blocker when one exists."""
    quoted_bid_id = quote(bid_id)
    if condition_id == "g1.owner_pursuit_decided":
        return f"/bids/{quoted_bid_id}/owner-decision"
    if condition_id == "g4.margin_approved":
        return f"/decisions?bid_id={quoted_bid_id}&approval_type=MARGIN#gate-approval"
    if condition_id in {
        "g4.required_approvals",
        "g4.high_findings_have_authority",
        "g6.senior_approvals_current",
    }:
        return f"/decisions?bid_id={quoted_bid_id}#gate-approval"
    return gate_destination(bid_id, gate)


def _next_gate(gate: Gate) -> Gate | None:
    gates = list(Gate)
    index = gates.index(gate)
    return gates[index + 1] if index + 1 < len(gates) else None


def classification_controls_for_level(level: BidLevel) -> list[ClassificationControl]:
    """Describe controls using the same Level rules as the gate engine."""
    bid_decision_required = level is not BidLevel.LEVEL_0
    margin_required = level in {
        BidLevel.LEVEL_2,
        BidLevel.LEVEL_3,
        BidLevel.LEVEL_4,
    }
    return [
        ClassificationControl(
            gate=Gate.G0,
            label="Complete Bid setup",
            required=True,
            explanation="Required for every classification.",
        ),
        ClassificationControl(
            gate=Gate.G1,
            label="Document the bid / no-bid decision",
            required=bid_decision_required,
            explanation=(
                "Required by the current gate rule."
                if bid_decision_required
                else "Not required for Level 0 by the current gate rule."
            ),
        ),
        ClassificationControl(
            gate=Gate.G4,
            label="Obtain margin approval",
            required=margin_required,
            explanation=(
                "Required for Level 2, Level 3 and Level 4 by the temporary "
                "governance-level rule; value and margin thresholds are not enforced."
                if margin_required
                else "Not required for Level 0 or Level 1 by the temporary "
                "governance-level rule; value and margin thresholds are not enforced."
            ),
        ),
    ]


def classification_controls(bid: Bid) -> list[ClassificationControl]:
    return classification_controls_for_level(bid.classification)


def governance_level_guides() -> list[GovernanceLevelGuide]:
    """Explain each supported level without creating policy beyond current gate rules."""
    names = (
        "Intake control",
        "Bid / no-bid control",
        "Margin control",
        "Enhanced pursuit governance",
        "Highest trigger-led governance",
    )
    guides: list[GovernanceLevelGuide] = []
    levels = list(BidLevel)
    for index, level in enumerate(levels):
        controls = classification_controls_for_level(level)
        required = tuple(control.label for control in controls if control.required)
        lower = levels[index - 1] if index else None
        higher = levels[index + 1] if index + 1 < len(levels) else None
        lower_required = (
            {item.label for item in classification_controls_for_level(lower) if item.required}
            if lower
            else set()
        )
        higher_required = (
            {item.label for item in classification_controls_for_level(higher) if item.required}
            if higher
            else set()
        )
        added_here = [item for item in required if item not in lower_required]
        added_above = [item for item in higher_required if item not in set(required)]
        difference_parts = []
        if added_here:
            difference_parts.append(f"Adds {', '.join(added_here)} versus the level below.")
        elif lower:
            difference_parts.append("No additional gate control is defined versus the level below.")
        else:
            difference_parts.append("This is the lowest governance floor.")
        if added_above:
            difference_parts.append(f"The next level adds {', '.join(added_above)}.")
        elif higher:
            difference_parts.append(
                "The next level has no additional gate control currently defined."
            )
        else:
            difference_parts.append("There is no higher supported level.")
        guides.append(
            GovernanceLevelGuide(
                level=level,
                name=names[index],
                meaning=(
                    "The deterministic Bid facts set this as the minimum governance floor; "
                    "the controls below come from the existing gate rules."
                ),
                controls=required,
                adjacent_difference=" ".join(difference_parts),
                next_action=(
                    "Assess the Bid facts, then select this level or a higher level with a reason. "
                    "A lower level is not permitted."
                ),
            )
        )
    return guides


def _blocker_view(bid: Bid, report_blocker: Blocker) -> BidBlockerView:
    gate = report_blocker.gate
    due_date = bid.customer_due_date if gate in {Gate.G6, Gate.G7} else bid.internal_due_date
    return BidBlockerView(
        condition_id=report_blocker.condition_id,
        gate=gate,
        heading=blocker_heading(report_blocker.condition_id, report_blocker.description),
        description=report_blocker.description,
        detail=report_blocker.detail,
        consequence=_GATE_CONSEQUENCE[gate],
        owner=bid.bc_owner,
        owing_party=_GATE_OWING_PARTY[gate],
        due_date=due_date,
        evidence_source="Existing gate and authoritative control registers",
        destination=(
            f"/decisions?bid_id={quote(bid.bid_id)}#authority-policies"
            if report_blocker.condition_id == "g6.senior_approvals_current"
            and "APPROVAL_POLICY_NOT_CONFIGURED" in report_blocker.detail
            else blocker_destination(bid.bid_id, report_blocker.condition_id, gate)
        ),
    )


def _active_blockers(bid: Bid, readiness: ReadinessReport) -> list[BidBlockerView]:
    return [_blocker_view(bid, blocker) for blocker in readiness.blockers if not blocker.overridden]


def _active_issue_blockers(bid: Bid, readiness: ReadinessReport) -> list[BidBlockerView]:
    return [
        _blocker_view(bid, blocker)
        for blocker in readiness.issue_blockers
        if not blocker.overridden
    ]


def workflow_development_blockers(
    blockers: Sequence[BidBlockerView],
    *,
    package_received: bool,
    evidence: OwnerDecisionEvidenceSnapshot,
) -> list[BidBlockerView]:
    """Hide G1 from presentation until review and initial assessment are complete.

    This does not alter the readiness report.  Proposal controls still consume the
    authoritative development blocker while the workflow presents the earlier
    review or assessment action.
    """
    decision_step_reached = (
        package_received
        and evidence.received_files.undecided == 0
        and initial_requirements_assessment_available(evidence)
    )
    return [
        blocker
        for blocker in blockers
        if blocker.condition_id != "g1.owner_pursuit_decided" or decision_step_reached
    ]


def handover_gate_blockers(
    blockers: Sequence[BidBlockerView], readiness: ReadinessReport
) -> list[str]:
    """Return every blocker line handover must consume, development and issue phase.

    Handover is the last step of the controlled issue chain, so pending senior
    approvals (issue-phase conditions the readiness verdict deliberately excludes
    so drafting can proceed) still make it not ready.
    """
    lines = [f"{blocker.heading} — {blocker.detail}" for blocker in blockers]
    lines.extend(
        f"Pending before issue — {blocker.description} {blocker.detail}".strip()
        for blocker in readiness.issue_blockers
        if not blocker.overridden
    )
    return lines


def _next_action(
    bid: Bid,
    blockers: list[BidBlockerView],
    waiting: list[WorkItem],
    waiting_controls: list[dict[str, str]],
    decisions: list[dict[str, str]],
) -> tuple[str, str]:
    if blockers:
        blocker = blockers[0]
        return f"Resolve: {blocker.heading}", blocker.destination
    if waiting:
        item = waiting[0]
        return f"Follow up: {item.title}", f"/my-work/{item.work_item_id}"
    if waiting_controls:
        return (
            "Follow up on the outstanding manufacturer or supplier response",
            f"/bids/{bid.bid_id}/manufacturers-coverage",
        )
    if decisions:
        return "Complete the pending decision or approval", f"/decisions?bid_id={bid.bid_id}"
    next_gate = _next_gate(bid.current_gate)
    if next_gate is None:
        return "Review the award and handover record", gate_destination(bid.bid_id, Gate.G7)
    return f"Prepare for {GATE_LABELS[next_gate]}", gate_destination(bid.bid_id, next_gate)


def _not_yet_assessed(
    readiness: ReadinessReport,
    workflow: BidWorkflowProjection | None,
) -> tuple[str, ...]:
    """Combine missing gate registers with zero-count workflow stages."""
    facts = list(readiness.not_yet_assessed)
    if workflow is not None:
        for key, label in (("requirements", "requirements"), ("scope", "scope")):
            stage = next((item for item in workflow.stages if item.key == key), None)
            if stage is not None and stage.status is StageStatus.NOT_STARTED:
                facts.append(label)
    return tuple(dict.fromkeys(facts))


def project_bid_workspace(
    bid: Bid,
    readiness: ReadinessReport,
    work_items: list[WorkItem],
    approval_attention: list[dict[str, str]],
    as_of: date,
    supplier_attention: list[dict[str, str]] | None = None,
    workflow: BidWorkflowProjection | None = None,
) -> BidWorkspaceProjection:
    """Build one workspace shell from authoritative projections only."""
    waiting = sorted(
        [
            item
            for item in work_items
            if item.bid_id == bid.bid_id and item.status is WorkItemStatus.WAITING
        ],
        key=lambda item: (item.chase_date or date.max, item.work_item_id),
    )
    decisions = sorted(
        [item for item in approval_attention if item.get("bid_id") == bid.bid_id],
        key=lambda item: (item.get("entity_id", ""), item.get("code", "")),
    )
    waiting_controls = sorted(
        [
            item
            for item in supplier_attention or []
            if item.get("bid_id") == bid.bid_id
            and ("NO_RESPONSE" in item.get("code", "") or "SILENT" in item.get("code", ""))
        ],
        key=lambda item: (item.get("entity_id", ""), item.get("code", "")),
    )
    blockers = _active_blockers(bid, readiness)
    issue_blockers = _active_issue_blockers(bid, readiness)
    if workflow is None:
        next_action, destination = _next_action(
            bid,
            blockers,
            waiting,
            waiting_controls,
            decisions,
        )
    else:
        next_action = workflow.next_action_label
        destination = workflow.next_action_path
    next_gate = _next_gate(bid.current_gate)
    return BidWorkspaceProjection(
        as_of=as_of,
        bid=bid,
        readiness=readiness,
        development_gate_label=readiness.development_gate_label,
        not_yet_assessed=_not_yet_assessed(readiness, workflow),
        pending_before_issue_count=readiness.pending_before_issue_count,
        blockers=blockers,
        issue_blockers=issue_blockers,
        waiting_items=waiting,
        waiting_control_attention=waiting_controls,
        decision_attention=decisions,
        classification_controls=classification_controls(bid),
        current_gate_label=GATE_LABELS[bid.current_gate],
        current_gate_description=GATE_DESCRIPTIONS[bid.current_gate],
        next_gate=next_gate,
        next_gate_label=GATE_LABELS[next_gate] if next_gate else None,
        next_action=next_action,
        next_action_destination=destination,
        workflow=workflow,
    )


_COMMERCIAL_POSITIONS_EXPLANATION = (
    "These positions are created when commercial factors are initialized in Commercial "
    "(an explicit action; opening a page never creates them). Each needs an owner and a "
    "disposition before gate G4 can pass. This is not a Package Intake step."
)


def outstanding_items(
    blockers: Sequence[BidBlockerView],
    *,
    issue_blockers: Sequence[BidBlockerView] = (),
    intake_attention: Sequence[object] = (),
    package_imported: bool = True,
    bid_id: str = "",
    owner: str = "",
    due_date: date | None = None,
    intake: PackageIntakeFacts | None = None,
) -> list[OutstandingItem]:
    """Merge every outstanding thing into one list stated as what is missing.

    The gate engine, the package-intake attention rules and their destinations are
    unchanged; this only rewrites presentation so the same blocker is never
    explained more than once on a page.

    Args:
        blockers: Active TASK-06 blockers for this Bid, most severe first.
        intake_attention: OPS-11 ``IntakeAttention`` records for this Bid.
        package_imported: Whether any customer package has been received.
        bid_id: The Bid whose resolve links are being built.
        owner: The accountable Bid owner.
        due_date: The internal due date, where one is meaningful.

    Returns:
        One deduplicated list, blocking items first, then stable by heading.
    """
    items: list[OutstandingItem] = []
    if bid_id and not package_imported:
        items.append(
            OutstandingItem(
                heading=PACKAGE_NOT_IMPORTED_HEADING,
                detail="No customer package has been received into this Bid.",
                consequence=(
                    "Requirements, scope and the Bid Basis have no controlled customer source."
                ),
                owner=owner,
                owing_party="Bid owner",
                due_date=due_date,
                evidence_source="Package intake register",
                resolve_label="Import customer bid package",
                resolve_path=f"/bids/{bid_id}/package-intake-addenda/import?mode=initial",
                blocking=True,
                code="PACKAGE_NOT_IMPORTED",
                scope="package_intake",
            )
        )
    for blocker in blockers:
        resolve_label = {
            "g1.owner_pursuit_decided": "Record owner decision",
            "g4.margin_approved": "Record margin approval",
        }.get(blocker.condition_id, "Resolve in this Bid")
        detail = blocker.detail
        if blocker.condition_id == "g4.commercial_review_clear":
            detail = f"{detail} {_COMMERCIAL_POSITIONS_EXPLANATION}"
        items.append(
            OutstandingItem(
                heading=blocker.heading,
                detail=detail,
                consequence=blocker.consequence,
                owner=blocker.owner,
                owing_party=blocker.owing_party,
                due_date=blocker.due_date,
                evidence_source=blocker.evidence_source,
                resolve_label=resolve_label,
                resolve_path=blocker.destination,
                blocking=True,
                code=blocker.condition_id,
            )
        )
    for blocker in issue_blockers:
        resolve_label = (
            "Record margin approval"
            if blocker.condition_id == "g4.margin_approved"
            else "Record senior approval"
        )
        items.append(
            OutstandingItem(
                heading=blocker.heading,
                detail=blocker.detail,
                consequence=blocker.consequence,
                owner=blocker.owner,
                owing_party=blocker.owing_party,
                due_date=blocker.due_date,
                evidence_source=blocker.evidence_source,
                resolve_label=resolve_label,
                resolve_path=blocker.destination,
                blocking=False,
                code=blocker.condition_id,
                scope="pending_before_issue",
            )
        )
    for record in intake_attention:
        code = str(getattr(record, "code", ""))
        message = str(getattr(record, "message", ""))
        resolve_label = (
            "Review files, then confirm Bid Basis"
            if code == "RECEIVED_RELEASE_NOT_IN_BASIS"
            else "Resolve in Package intake"
        )
        resolve_path = str(getattr(record, "destination", ""))
        if code == "RECEIVED_RELEASE_NOT_IN_BASIS" and intake is not None:
            resolve_label, resolve_path = intake_control_action(bid_id, intake)
        items.append(
            OutstandingItem(
                heading=intake_heading(code, message, getattr(record, "release_type", None)),
                detail=message,
                consequence=(
                    "Proposal issue is blocked until this is resolved."
                    if bool(getattr(record, "blocking", False))
                    else "The Bid Basis may not reflect every customer release."
                ),
                owner=owner,
                owing_party="Bid owner",
                due_date=due_date,
                evidence_source="Package intake register",
                resolve_label=resolve_label,
                resolve_path=resolve_path,
                blocking=bool(getattr(record, "blocking", False)),
                code=code or None,
                scope="bid_basis" if code == "RECEIVED_RELEASE_NOT_IN_BASIS" else "package_intake",
            )
        )
    seen: set[str] = set()
    unique: list[OutstandingItem] = []
    for item in items:
        if item.heading in seen:
            continue
        seen.add(item.heading)
        unique.append(item)
    unique.sort(key=lambda item: (not item.blocking,))
    return unique


def _deadline_attention(
    bid: Bid,
    as_of: date,
    upcoming_horizon_days: int,
) -> BidDeadlineAttention:
    if bid.status in CURRENT_BID_STATUSES and bid.internal_due_date < as_of:
        return BidDeadlineAttention.OVERDUE
    if bid.status in CURRENT_BID_STATUSES and as_of <= bid.customer_due_date <= as_of + timedelta(
        days=upcoming_horizon_days
    ):
        return BidDeadlineAttention.UPCOMING
    return BidDeadlineAttention.NONE


def _is_dormant(last_activity: datetime | None, as_of: date) -> bool:
    if last_activity is None:
        return False
    return (as_of - last_activity.date()).days >= DORMANT_AFTER_DAYS


def _portfolio_tier(
    bid: Bid,
    readiness: ReadinessReport,
    deadline: BidDeadlineAttention,
    decision_count: int,
) -> int:
    if bid.status in HISTORY_BID_STATUSES:
        return 6
    if bid.status is BidStatus.SUBMITTED:
        return 5
    if bid.status is BidStatus.HELD or readiness.verdict in {
        ReadinessVerdict.HOLD,
        ReadinessVerdict.ESCALATE,
    }:
        return 0
    if deadline is BidDeadlineAttention.OVERDUE:
        return 1
    if deadline is BidDeadlineAttention.UPCOMING:
        return 2
    if decision_count:
        return 3
    return 4


def _other_attention_count(
    bid_id: str,
    requirement_attention: list[ProjectedRequirementAttention],
    deliverable_attention: list[dict[str, str]],
    commercial_attention: list[dict[str, str]],
    contract_risk_attention: list[dict[str, str]],
    intake_attention: list[dict[str, str]],
) -> int:
    """Count non-blocker attention already surfaced elsewhere in My Day for one Bid."""
    count = sum(1 for item in requirement_attention if item.requirement.bid_id == bid_id)
    for collection in (
        deliverable_attention,
        commercial_attention,
        contract_risk_attention,
        intake_attention,
    ):
        count += sum(1 for item in collection if item.get("bid_id") == bid_id)
    return count


def _supplier_response_position(bid_id: str, supplier_attention: list[dict[str, str]]) -> str:
    """Business-worded supplier-response state, derived from already-loaded attention."""
    items = [item for item in supplier_attention if item.get("bid_id") == bid_id]
    if not items:
        return "Clear"
    if any(
        "NO_RESPONSE" in item.get("code", "") or "SILENT" in item.get("code", "") for item in items
    ):
        return "Awaiting supplier response"
    return "Supplier attention open"


def _overdue_work_count(bid_work: list[WorkItem], as_of: date) -> int:
    return sum(
        1
        for item in bid_work
        if any(reason.endswith("OVERDUE") for reason in work_item_attention_reasons(item, as_of))
    )


def _my_day_bucket(
    bid: Bid,
    blockers: list[BidBlockerView],
    readiness: ReadinessReport,
    deadline: BidDeadlineAttention,
    decisions: list[dict[str, str]],
    waiting_count: int,
    overdue_work_count: int,
    other_attention_count: int,
) -> MyDayBidBucket:
    """Classify one Bid into exactly one live My Day bucket.

    A Bid is never WAITING merely because a waiting record exists: REQUIRES_ACTION is
    checked first and wins over WAITING whenever Jason has a higher-priority direct action.
    """
    if bid.status in HISTORY_BID_STATUSES:
        return MyDayBidBucket.HISTORY
    requires_action = (
        bool(blockers)
        or readiness.verdict is not ReadinessVerdict.CLEAR
        or deadline is BidDeadlineAttention.OVERDUE
        or bool(decisions)
        or overdue_work_count > 0
        or other_attention_count > 0
    )
    if requires_action:
        return MyDayBidBucket.REQUIRES_ACTION
    if waiting_count > 0:
        return MyDayBidBucket.WAITING
    return MyDayBidBucket.ON_TRACK_OR_UPCOMING


def _evidence_links(
    bid: Bid,
    *,
    requirement_count: int,
    supplier_or_waiting_controls: bool,
    commercial_or_contract_risk_count: int,
    intake_count: int,
    decisions: list[dict[str, str]],
    waiting: list[WorkItem],
) -> list[BidEvidenceLink]:
    """Only link to a Bid section whose underlying signal is actually present."""
    links = [BidEvidenceLink(label="Bid overview", href=f"/bids/{bid.bid_id}")]
    if requirement_count:
        links.append(
            BidEvidenceLink(
                label="Requirements and scope",
                href=f"/bids/{bid.bid_id}/requirements-scope",
            )
        )
    if supplier_or_waiting_controls:
        links.append(
            BidEvidenceLink(
                label="Manufacturers and coverage",
                href=f"/bids/{bid.bid_id}/manufacturers-coverage",
            )
        )
    if commercial_or_contract_risk_count:
        links.append(
            BidEvidenceLink(
                label="Commercial and contract",
                href=f"/bids/{bid.bid_id}/commercial-contract",
            )
        )
    if intake_count:
        links.append(
            BidEvidenceLink(
                label="Package intake and addenda",
                href=f"/bids/{bid.bid_id}/package-intake-addenda",
            )
        )
    if decisions:
        links.append(
            BidEvidenceLink(
                label="Decisions and approvals",
                href=f"/decisions?bid_id={bid.bid_id}",
            )
        )
    if waiting:
        links.append(
            BidEvidenceLink(
                label="Waiting work items",
                href=f"/my-work?bid_id={bid.bid_id}",
            )
        )
    return links


def _view_includes(
    view: BidPortfolioView,
    bid: Bid,
    deadline: BidDeadlineAttention,
    dormant: bool,
) -> bool:
    if view is BidPortfolioView.ALL:
        return True
    if view is BidPortfolioView.CURRENT:
        return bid.status in CURRENT_BID_STATUSES
    if view is BidPortfolioView.HISTORY:
        return bid.status in HISTORY_BID_STATUSES
    if view is BidPortfolioView.ISSUED:
        return bid.status is BidStatus.SUBMITTED
    if view is BidPortfolioView.UPCOMING:
        return bid.status in {BidStatus.ACTIVE, BidStatus.HELD} and deadline is (
            BidDeadlineAttention.UPCOMING
        )
    return bid.status in {BidStatus.ACTIVE, BidStatus.HELD} and dormant


def last_activity_by_bid(
    audit_entries: list[AuditEntry],
    bids: list[Bid],
) -> dict[str, datetime]:
    """Pure reduction of already-loaded audit rows to one timestamp per Bid.

    ``audit_entries`` must already be ordered ascending by timestamp (as
    ``BidRepository.list_audit`` returns them) so the last write per ``bid_id`` in
    iteration order is the maximum. Bids with no audit rows fall back to
    ``Bid.updated_at``. This reports the last *recorded* audit activity — it does not
    distinguish human actions from system-generated ones (for example gate
    re-evaluations), because the codebase has no existing classification of audit
    action strings into human versus system origin to reuse.
    """
    latest: dict[str, datetime] = {}
    for entry in audit_entries:
        if entry.bid_id is not None:
            latest[entry.bid_id] = entry.timestamp
    for bid in bids:
        latest.setdefault(bid.bid_id, bid.updated_at)
    return latest


def project_bid_portfolio(
    bids: list[Bid],
    readiness_by_bid: dict[str, ReadinessReport],
    work_items: list[WorkItem],
    approval_attention: list[dict[str, str]],
    filters: BidPortfolioFilters,
    as_of: date,
    upcoming_horizon_days: int = 14,
    supplier_attention: list[dict[str, str]] | None = None,
    requirement_attention: list[ProjectedRequirementAttention] | None = None,
    deliverable_attention: list[dict[str, str]] | None = None,
    commercial_attention: list[dict[str, str]] | None = None,
    contract_risk_attention: list[dict[str, str]] | None = None,
    intake_attention: list[dict[str, str]] | None = None,
    last_activity: dict[str, datetime] | None = None,
    workflow_by_bid: dict[str, BidWorkflowProjection] | None = None,
) -> BidPortfolioProjection:
    """Filter and rank bids deterministically without reading storage or a clock."""
    supplier_attention = supplier_attention or []
    requirement_attention = requirement_attention or []
    deliverable_attention = deliverable_attention or []
    commercial_attention = commercial_attention or []
    contract_risk_attention = contract_risk_attention or []
    intake_attention = intake_attention or []
    last_activity = last_activity or {}
    rows: list[BidPortfolioRow] = []
    for bid in bids:
        readiness = readiness_by_bid[bid.bid_id]
        deadline = _deadline_attention(bid, as_of, upcoming_horizon_days)
        bid_last_activity = last_activity.get(bid.bid_id, bid.updated_at)
        dormant = _is_dormant(bid_last_activity, as_of)
        if not _view_includes(filters.view, bid, deadline, dormant):
            continue
        if filters.status is not None and bid.status is not filters.status:
            continue
        if filters.classification is not None and bid.classification is not filters.classification:
            continue
        if filters.owner and filters.owner.casefold() not in bid.bc_owner.casefold():
            continue
        if (
            filters.readiness is not BidReadinessFilter.ANY
            and readiness.verdict.value != filters.readiness.value
        ):
            continue
        if filters.deadline is not BidDeadlineAttention.ANY and deadline is not filters.deadline:
            continue
        bid_work = [item for item in work_items if item.bid_id == bid.bid_id]
        waiting = [item for item in bid_work if item.status is WorkItemStatus.WAITING]
        waiting_controls = [
            item
            for item in supplier_attention
            if item.get("bid_id") == bid.bid_id
            and ("NO_RESPONSE" in item.get("code", "") or "SILENT" in item.get("code", ""))
        ]
        decisions = [item for item in approval_attention if item.get("bid_id") == bid.bid_id]
        blockers = _active_blockers(bid, readiness)
        workflow = (workflow_by_bid or {}).get(bid.bid_id)
        owner_stage = (
            next((stage for stage in workflow.stages if stage.key == "owner-decision"), None)
            if workflow is not None
            else None
        )
        if owner_stage is not None and owner_stage.status is StageStatus.NOT_STARTED:
            # Review and the initial assessment come first: the portfolio headline must not
            # present the owner decision as a blocker before that step can be performed.
            blockers = [b for b in blockers if b.condition_id != "g1.owner_pursuit_decided"]
        if workflow is None:
            next_action, destination = _next_action(
                bid,
                blockers,
                waiting,
                waiting_controls,
                decisions,
            )
        else:
            next_action = workflow.next_action_label
            destination = workflow.next_action_path
        requirement_count = sum(
            1 for item in requirement_attention if item.requirement.bid_id == bid.bid_id
        )
        commercial_or_contract_risk_count = sum(
            1
            for item in (*commercial_attention, *contract_risk_attention)
            if item.get("bid_id") == bid.bid_id
        )
        intake_count = sum(1 for item in intake_attention if item.get("bid_id") == bid.bid_id)
        other_attention_count = _other_attention_count(
            bid.bid_id,
            requirement_attention,
            deliverable_attention,
            commercial_attention,
            contract_risk_attention,
            intake_attention,
        )
        overdue_work_count = _overdue_work_count(bid_work, as_of)
        rows.append(
            BidPortfolioRow(
                bid=bid,
                readiness=readiness,
                development_gate_label=readiness.development_gate_label,
                not_yet_assessed=_not_yet_assessed(readiness, workflow),
                pending_before_issue_count=readiness.pending_before_issue_count,
                highest_blocker=blockers[0] if blockers else None,
                additional_blocker_count=max(len(blockers) - 1, 0),
                waiting_count=len(waiting) + len(waiting_controls),
                overdue_work_count=overdue_work_count,
                decision_count=len(decisions),
                other_attention_count=other_attention_count,
                supplier_response_position=_supplier_response_position(
                    bid.bid_id, supplier_attention
                ),
                last_activity=bid_last_activity,
                next_action=next_action,
                next_action_destination=destination,
                secondary_action=workflow.secondary_action_label if workflow else None,
                secondary_action_destination=workflow.secondary_action_path if workflow else None,
                stopped_reason=workflow.stopped_reason if workflow is not None else None,
                deadline_attention=deadline,
                sort_tier=_portfolio_tier(bid, readiness, deadline, len(decisions)),
                my_day_bucket=_my_day_bucket(
                    bid,
                    blockers,
                    readiness,
                    deadline,
                    decisions,
                    len(waiting) + len(waiting_controls),
                    overdue_work_count,
                    other_attention_count,
                ),
                evidence_links=_evidence_links(
                    bid,
                    requirement_count=requirement_count,
                    supplier_or_waiting_controls=bool(waiting_controls),
                    commercial_or_contract_risk_count=commercial_or_contract_risk_count,
                    intake_count=intake_count,
                    decisions=decisions,
                    waiting=waiting,
                ),
            )
        )
    rows.sort(
        key=lambda row: (
            row.sort_tier,
            row.bid.internal_due_date if row.sort_tier == 1 else row.bid.customer_due_date,
            row.bid.project_name.casefold(),
            row.bid.bid_id,
        )
    )
    return BidPortfolioProjection(as_of=as_of, filters=filters, rows=rows)


class BidControlCenterService:
    """Batch presentation boundary over existing Bid and My Day sources."""

    def __init__(
        self,
        bid_repository: BidRepository,
        work_repository: WorkItemRepository,
        my_day_loader: Callable[[date], MyDayProjection],
        readiness_loader: Callable[[str], ReadinessReport],
        workspace_attention_loader: Callable[[str, date], BidWorkspaceAttention],
        workflow_loader: Callable[
            [Bid, ReadinessReport, list[BidBlockerView], list[BidBlockerView]],
            BidWorkflowProjection,
        ]
        | None = None,
    ) -> None:
        self.bid_repository = bid_repository
        self.work_repository = work_repository
        self._my_day_loader = my_day_loader
        self._readiness_loader = readiness_loader
        self._workspace_attention_loader = workspace_attention_loader
        self._workflow_loader = workflow_loader

    def portfolio(
        self,
        filters: BidPortfolioFilters,
        *,
        as_of: date,
    ) -> BidPortfolioProjection:
        bids = self.bid_repository.list_bids()
        day = self._my_day_loader(as_of)
        return build_bid_portfolio(
            day,
            bids,
            self.work_repository.list(active_only=True),
            self.bid_repository.list_audit(),
            filters,
            as_of,
            workflow_loader=self._workflow_loader,
        )

    def workspace(self, bid_id: str, *, as_of: date) -> BidWorkspaceProjection:
        bid = self.bid_repository.get_bid(bid_id)
        if bid is None:
            raise BidNotFoundError(f"Bid not found: {bid_id}")
        readiness = self._readiness_loader(bid_id)
        attention = self._workspace_attention_loader(bid_id, as_of)
        blockers = _active_blockers(bid, readiness)
        issue_blockers = _active_issue_blockers(bid, readiness)
        workflow = (
            self._workflow_loader(bid, readiness, blockers, issue_blockers)
            if self._workflow_loader is not None
            else None
        )
        return project_bid_workspace(
            bid,
            readiness,
            self.work_repository.list(bid_id=bid_id, active_only=True),
            attention.approval_attention,
            as_of,
            supplier_attention=attention.supplier_attention,
            workflow=workflow,
        )


def build_bid_portfolio(
    day: MyDayProjection,
    bids: list[Bid],
    work_items: list[WorkItem],
    audit_entries: list[AuditEntry],
    filters: BidPortfolioFilters,
    as_of: date,
    upcoming_horizon_days: int = 14,
    workflow_loader: Callable[
        [Bid, ReadinessReport, list[BidBlockerView], list[BidBlockerView]],
        BidWorkflowProjection,
    ]
    | None = None,
) -> BidPortfolioProjection:
    """Build the shared Bid-portfolio projection from already-loaded batch sources.

    Callers that already hold a `MyDayProjection` for this `as_of` (the `/my-day` route)
    must pass it in here rather than triggering a second `MyDayService.get_my_day()` call,
    which repeats that service's own internal per-Bid supplier/deliverable-attention
    queries. `BidControlCenterService.portfolio()` is the only caller allowed to load a
    fresh projection, for callers (the `/bids` route) that do not already have one.
    """
    readiness = {item.bid_id: item.report for item in day.readiness_reports}
    workflow_by_bid = (
        {
            bid.bid_id: workflow_loader(
                bid,
                readiness[bid.bid_id],
                _active_blockers(bid, readiness[bid.bid_id]),
                _active_issue_blockers(bid, readiness[bid.bid_id]),
            )
            for bid in bids
        }
        if workflow_loader is not None
        else None
    )
    return project_bid_portfolio(
        bids,
        readiness,
        work_items,
        day.approval_attention,
        filters,
        as_of,
        upcoming_horizon_days=upcoming_horizon_days,
        supplier_attention=day.supplier_attention,
        requirement_attention=day.requirement_attention,
        deliverable_attention=day.deliverable_attention,
        commercial_attention=day.commercial_attention,
        contract_risk_attention=day.contract_risk_attention,
        intake_attention=day.intake_attention,
        last_activity=last_activity_by_bid(audit_entries, bids),
        workflow_by_bid=workflow_by_bid,
    )


__all__ = [
    "CURRENT_BID_STATUSES",
    "DORMANT_AFTER_DAYS",
    "HISTORY_BID_STATUSES",
    "BidControlCenterService",
    "BidDeadlineAttention",
    "BidEvidenceLink",
    "BidNotFoundError",
    "BidPortfolioFilters",
    "BidPortfolioView",
    "BidReadinessFilter",
    "BidWorkspaceSection",
    "BidWorkspaceAttention",
    "GovernanceLevelGuide",
    "MyDayBidBucket",
    "build_bid_portfolio",
    "classification_controls",
    "classification_controls_for_level",
    "governance_level_guides",
    "last_activity_by_bid",
    "outstanding_items",
    "project_bid_portfolio",
    "project_bid_workspace",
    "workspace_path",
    "workflow_development_blockers",
]
