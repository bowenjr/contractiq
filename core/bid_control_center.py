"""Typed presentation projections for the role-aligned bid control centre."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.bid_repository import BidRepository
from core.enums import BidLevel, BidStatus, Gate
from core.my_day import MyDayProjection
from core.readiness import Blocker, ReadinessReport, ReadinessVerdict
from core.schemas import Bid
from core.work_item_repository import WorkItemRepository
from core.work_items import WorkItem, WorkItemStatus


class BidPortfolioView(StrEnum):
    CURRENT = "current"
    HISTORY = "history"
    ALL = "all"


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
    REQUIREMENTS_SCOPE = "requirements-scope"
    MANUFACTURERS_COVERAGE = "manufacturers-coverage"
    COMMERCIAL_CONTRACT = "commercial-contract"
    PROPOSAL_NEGOTIATION = "proposal-negotiation"
    AWARD_HANDOVER = "award-handover"


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
    description: str
    detail: str
    consequence: str
    owner: str
    owing_party: str
    due_date: date | None
    evidence_source: str
    destination: str


class BidPortfolioRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bid: Bid
    readiness: ReadinessReport
    highest_blocker: BidBlockerView | None
    waiting_count: int
    decision_count: int
    next_action: str
    next_action_destination: str
    deadline_attention: BidDeadlineAttention
    sort_tier: int = Field(ge=0, le=6)


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
    blockers: list[BidBlockerView]
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
    Gate.G0: "Intake",
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
            label="Complete Bid intake",
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
                "Required for Level 2, Level 3 and Level 4 by the current gate rule."
                if margin_required
                else "Not required for Level 0 or Level 1 by the current gate rule."
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
        description=report_blocker.description,
        detail=report_blocker.detail,
        consequence=_GATE_CONSEQUENCE[gate],
        owner=bid.bc_owner,
        owing_party=_GATE_OWING_PARTY[gate],
        due_date=due_date,
        evidence_source="Existing gate and authoritative control registers",
        destination=gate_destination(bid.bid_id, gate),
    )


def _active_blockers(bid: Bid, readiness: ReadinessReport) -> list[BidBlockerView]:
    return [_blocker_view(bid, blocker) for blocker in readiness.blockers if not blocker.overridden]


def _next_action(
    bid: Bid,
    blockers: list[BidBlockerView],
    waiting: list[WorkItem],
    waiting_controls: list[dict[str, str]],
    decisions: list[dict[str, str]],
) -> tuple[str, str]:
    if blockers:
        blocker = blockers[0]
        return f"Resolve: {blocker.description}", blocker.destination
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


def project_bid_workspace(
    bid: Bid,
    readiness: ReadinessReport,
    work_items: list[WorkItem],
    approval_attention: list[dict[str, str]],
    as_of: date,
    supplier_attention: list[dict[str, str]] | None = None,
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
    next_action, destination = _next_action(
        bid,
        blockers,
        waiting,
        waiting_controls,
        decisions,
    )
    next_gate = _next_gate(bid.current_gate)
    return BidWorkspaceProjection(
        as_of=as_of,
        bid=bid,
        readiness=readiness,
        blockers=blockers,
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
    )


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


def _view_includes(view: BidPortfolioView, status: BidStatus) -> bool:
    if view is BidPortfolioView.ALL:
        return True
    if view is BidPortfolioView.CURRENT:
        return status in CURRENT_BID_STATUSES
    return status in HISTORY_BID_STATUSES


def project_bid_portfolio(
    bids: list[Bid],
    readiness_by_bid: dict[str, ReadinessReport],
    work_items: list[WorkItem],
    approval_attention: list[dict[str, str]],
    filters: BidPortfolioFilters,
    as_of: date,
    upcoming_horizon_days: int = 14,
    supplier_attention: list[dict[str, str]] | None = None,
) -> BidPortfolioProjection:
    """Filter and rank bids deterministically without reading storage or a clock."""
    rows: list[BidPortfolioRow] = []
    for bid in bids:
        if not _view_includes(filters.view, bid.status):
            continue
        if filters.status is not None and bid.status is not filters.status:
            continue
        if filters.classification is not None and bid.classification is not filters.classification:
            continue
        if filters.owner and filters.owner.casefold() not in bid.bc_owner.casefold():
            continue
        readiness = readiness_by_bid[bid.bid_id]
        if (
            filters.readiness is not BidReadinessFilter.ANY
            and readiness.verdict.value != filters.readiness.value
        ):
            continue
        deadline = _deadline_attention(bid, as_of, upcoming_horizon_days)
        if filters.deadline is not BidDeadlineAttention.ANY and deadline is not filters.deadline:
            continue
        bid_work = [item for item in work_items if item.bid_id == bid.bid_id]
        waiting = [item for item in bid_work if item.status is WorkItemStatus.WAITING]
        waiting_controls = [
            item
            for item in supplier_attention or []
            if item.get("bid_id") == bid.bid_id
            and ("NO_RESPONSE" in item.get("code", "") or "SILENT" in item.get("code", ""))
        ]
        decisions = [item for item in approval_attention if item.get("bid_id") == bid.bid_id]
        blockers = _active_blockers(bid, readiness)
        next_action, destination = _next_action(
            bid,
            blockers,
            waiting,
            waiting_controls,
            decisions,
        )
        rows.append(
            BidPortfolioRow(
                bid=bid,
                readiness=readiness,
                highest_blocker=blockers[0] if blockers else None,
                waiting_count=len(waiting) + len(waiting_controls),
                decision_count=len(decisions),
                next_action=next_action,
                next_action_destination=destination,
                deadline_attention=deadline,
                sort_tier=_portfolio_tier(bid, readiness, deadline, len(decisions)),
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
    ) -> None:
        self.bid_repository = bid_repository
        self.work_repository = work_repository
        self._my_day_loader = my_day_loader
        self._readiness_loader = readiness_loader
        self._workspace_attention_loader = workspace_attention_loader

    def portfolio(
        self,
        filters: BidPortfolioFilters,
        *,
        as_of: date,
    ) -> BidPortfolioProjection:
        day = self._my_day_loader(as_of)
        readiness = {item.bid_id: item.report for item in day.readiness_reports}
        return project_bid_portfolio(
            self.bid_repository.list_bids(),
            readiness,
            self.work_repository.list(active_only=True),
            day.approval_attention,
            filters,
            as_of,
            supplier_attention=day.supplier_attention,
        )

    def workspace(self, bid_id: str, *, as_of: date) -> BidWorkspaceProjection:
        bid = self.bid_repository.get_bid(bid_id)
        if bid is None:
            raise BidNotFoundError(f"Bid not found: {bid_id}")
        readiness = self._readiness_loader(bid_id)
        attention = self._workspace_attention_loader(bid_id, as_of)
        return project_bid_workspace(
            bid,
            readiness,
            self.work_repository.list(bid_id=bid_id, active_only=True),
            attention.approval_attention,
            as_of,
            supplier_attention=attention.supplier_attention,
        )


__all__ = [
    "BidControlCenterService",
    "BidDeadlineAttention",
    "BidNotFoundError",
    "BidPortfolioFilters",
    "BidPortfolioView",
    "BidReadinessFilter",
    "BidWorkspaceSection",
    "BidWorkspaceAttention",
    "GovernanceLevelGuide",
    "classification_controls",
    "classification_controls_for_level",
    "governance_level_guides",
    "project_bid_portfolio",
    "project_bid_workspace",
    "workspace_path",
]
