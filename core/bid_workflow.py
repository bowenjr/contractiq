"""One presentation projection of the single Bid workflow spine.

This module creates no authority and holds no state. Every stage status, every
outstanding item and the single next required action are derived from facts that
authoritative sources have already produced:

* TASK-06 remains the only readiness engine; its verdict and blockers arrive here
  already evaluated.
* OPS-07W ``build_navigator`` remains the only source of stage completeness for
  the setup, document, requirement, scope, manufacturer, commercial and proposal
  stages; this module relabels and re-sequences that same projection.
* OPS-11 package intake remains the only source of received-package facts.

Nothing here reads storage, a database connection or a clock.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from core.ops07w import NavigatorStage

STAGE_COUNT = 9


class StageStatus(StrEnum):
    """The complete stage vocabulary shown anywhere in the Bid workspace."""

    NOT_STARTED = "Not started"
    IN_PROGRESS = "In progress"
    BLOCKED = "Blocked"
    READY_FOR_REVIEW = "Ready for review"
    COMPLETE = "Complete"
    NOT_APPLICABLE = "Not applicable"

    @property
    def slug(self) -> str:
        """Return a CSS-safe token for this status."""
        return self.name.lower().replace("_", "-")


# Statuses that need no further work from the Bid team at this stage.
_SETTLED = frozenset({StageStatus.COMPLETE, StageStatus.NOT_APPLICABLE})

# Statuses which, on a stage the work has not reached yet, must not advertise an
# action. A later stage that is merely waiting on its prerequisites is not a task.
_PREMATURE = frozenset({StageStatus.NOT_STARTED, StageStatus.BLOCKED})

_NAVIGATOR_STATUS: dict[str, StageStatus] = {
    "Not started": StageStatus.NOT_STARTED,
    "In progress": StageStatus.IN_PROGRESS,
    "Blocked": StageStatus.BLOCKED,
    "Complete": StageStatus.COMPLETE,
}

# Bid statuses that end pre-award pursuit work.
_PURSUIT_STOPPED = frozenset({"no_bid"})
_PURSUIT_DECIDED = frozenset({"won", "lost"})


class PackageIntakeFacts(BaseModel):
    """Received-package facts read from the OPS-11 intake summary only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    releases_received: int = Field(ge=0)
    releases_incorporated: int = Field(ge=0)
    files_requiring_review: int = Field(ge=0)
    expected_missing: int = Field(ge=0)
    basis_published: bool = False
    latest_release_id: str | None = None

    @property
    def not_incorporated(self) -> int:
        return max(self.releases_received - self.releases_incorporated, 0)


class WorkflowStage(BaseModel):
    """One stage of the nine-stage Bid workflow spine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    number: int = Field(ge=1, le=STAGE_COUNT)
    label: str
    status: StageStatus
    summary: str
    action_label: str
    action_path: str
    section_path: str
    actionable: bool
    blocked_reason: str | None = None

    @property
    def settled(self) -> bool:
        return self.status in _SETTLED


class OutstandingItem(BaseModel):
    """One thing that is missing, stated as what is missing — never as a positive."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    heading: str
    detail: str
    consequence: str
    owner: str
    owing_party: str
    due_date: date | None
    evidence_source: str
    resolve_label: str
    resolve_path: str
    blocking: bool


class BidWorkflowProjection(BaseModel):
    """The one workflow spine rendered by every Bid workspace section."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bid_id: str
    stages: tuple[WorkflowStage, ...]
    current_stage_key: str
    next_action_label: str
    next_action_path: str
    next_action_stage_label: str
    status_line: str
    readiness_line: str
    outstanding: tuple[OutstandingItem, ...]

    @property
    def current_stage(self) -> WorkflowStage:
        return next(stage for stage in self.stages if stage.key == self.current_stage_key)

    @property
    def blocking_count(self) -> int:
        return sum(1 for item in self.outstanding if item.blocking)


# --------------------------------------------------------------------------- #
# Blocker headings
# --------------------------------------------------------------------------- #
# The gate engine states each condition positively ("Bid/no-bid approval has been
# obtained."). A heading must say what is missing, so presentation maps each
# authoritative condition id to a short negative heading. The engine, the
# condition ids and the evaluated detail text are unchanged.
BLOCKER_HEADINGS: dict[str, str] = {
    "g0.bid_complete": "Bid setup incomplete",
    "g1.bid_no_bid_approved": "Bid/no-bid approval missing",
    "g2.no_scope_gaps": "Scope gap unresolved",
    "g2.strategy_recorded": "Bid strategy not recorded",
    "g3.suppliers_supported": "Supplier coverage unconfirmed",
    "g4.margin_approved": "Margin approval required",
    "g4.high_findings_have_authority": "Contract risk approval authority missing",
    "g4.required_approvals": "Required approval missing",
    "g4.commercial_review_clear": "Commercial position unresolved",
    "g5.mandatory_requirements_complete": "Requirement response incomplete",
    "g5.no_unconfirmed_material": "Analysis finding unconfirmed",
    "g5.prior_gates_passed": "Earlier gate incomplete",
    "g6.concessions_approved": "Concession approval missing",
    "g7.award_matches_offer": "Award reconciliation unresolved",
    "g7.handover_accepted": "Handover not accepted",
}

# Package-intake attention codes use the same negative-heading rule.
INTAKE_HEADINGS: dict[str, str] = {
    "EXPECTED_RELEASE_MISSING": "Expected customer release not received",
    "RELEASE_REFERENCE_MISSING": "Customer package reference missing",
    "RECEIVED_RELEASE_NOT_IN_BASIS": "Addendum not incorporated",
    "RELEASE_DATE_SEQUENCE_CONFLICT": "Customer release dates conflict",
    "ACKNOWLEDGEMENT_OUTSTANDING": "Addendum acknowledgement outstanding",
    "RELEASE_REFERENCE_CONFLICT": "Customer package reference conflicts",
    "PORTAL_EMAIL_DISCREPANCY": "Received channels disagree",
    "BASIS_DOCUMENT_VERSION_INVALID": "Bid Basis document version invalid",
}

# Proposal-readiness blocker codes use the same negative-heading rule.
PROPOSAL_HEADINGS: dict[str, str] = {
    "MISSING_REQUIREMENTS": "No customer requirement registered",
    "UNRESOLVED_REQUIREMENT": "Requirement response incomplete",
    "MISSING_PROPOSED_RESPONSE": "Proposed response missing",
    "MISSING_CONTROLLED_SOURCE": "Controlled customer document missing",
    "MISSING_SCOPE": "Bid scope not recorded",
    "UNRESOLVED_SCOPE": "Scope item unresolved",
    "UNRESOLVED_INTERFACE": "Interface unresolved",
    "MANUFACTURER_RESPONSE_BLOCKER": "Manufacturer response outstanding",
    "MISSING_MANUFACTURER_EVIDENCE": "Manufacturer evidence missing",
    "MANUFACTURER_EXCEPTION_UNDISPOSED": "Manufacturer exception undisposed",
    "MISSING_COMMERCIAL_POSITION": "Commercial position missing",
    "UNRESOLVED_COMMERCIAL_POSITION": "Commercial position unresolved",
    "UNDISPOSED_QUALIFICATION": "Commercial qualification undisposed",
    "BLOCKING_CONTRACT_RISK": "Contract risk unresolved",
    "MISSING_ACCOUNTABLE_OWNER": "Accountable owner missing",
    "MISSING_SELECTED_PRICING_SCENARIO": "Pricing scenario not selected",
    "MISSING_DELIVERY_COMMITMENT": "Delivery commitment missing",
    "UNRESOLVED_DELIVERY_COMMITMENT": "Delivery commitment unresolved",
}

PACKAGE_NOT_IMPORTED_HEADING = "Customer package not imported"


def blocker_heading(condition_id: str, fallback: str) -> str:
    """Return the negative heading for one authoritative gate condition."""
    return BLOCKER_HEADINGS.get(condition_id, fallback)


def intake_heading(code: str, fallback: str) -> str:
    """Return the negative heading for one package-intake attention code."""
    return INTAKE_HEADINGS.get(code, fallback)


def proposal_heading(code: str, fallback: str) -> str:
    """Return the negative heading for one proposal-readiness blocker code."""
    return PROPOSAL_HEADINGS.get(code, fallback)


# --------------------------------------------------------------------------- #
# Stage construction
# --------------------------------------------------------------------------- #
def _section(bid_id: str, section: str) -> str:
    return f"/bids/{bid_id}" if section == "overview" else f"/bids/{bid_id}/{section}"


def _navigator_status(stage: NavigatorStage | None) -> StageStatus:
    if stage is None:
        return StageStatus.NOT_STARTED
    return _NAVIGATOR_STATUS.get(stage.status, StageStatus.NOT_STARTED)


def _package_intake_stage(
    bid_id: str,
    intake: PackageIntakeFacts,
) -> tuple[StageStatus, str, str, str, str | None]:
    """Derive stage 2 from received-package facts alone.

    Receipt and review of customer files belong here. Publishing the Bid Basis is
    stage 9, so this stage never sends the user to incorporate a release before
    its files are under document control.
    """
    section = _section(bid_id, "package-intake-addenda")
    review_path = (
        f"{section}/releases/{intake.latest_release_id}#files"
        if intake.latest_release_id
        else section
    )
    if intake.releases_received == 0 and intake.expected_missing:
        return (
            StageStatus.BLOCKED,
            "A customer release is known to exist but no files have been received",
            "Import the expected customer release",
            f"{section}/import?mode=initial",
            "The customer package is expected but has not arrived.",
        )
    if intake.releases_received == 0:
        return (
            StageStatus.NOT_STARTED,
            "No customer package has been received",
            "Import customer bid package",
            f"{section}/import?mode=initial",
            None,
        )
    received = (
        f"{intake.releases_received} received package(s), "
        f"{intake.releases_incorporated} incorporated"
    )
    if intake.expected_missing:
        return (
            StageStatus.BLOCKED,
            f"{received}; {intake.expected_missing} expected release(s) not received",
            "Import the expected customer release",
            f"{section}/import?mode=addendum",
            "An expected customer release has not been received.",
        )
    if intake.files_requiring_review:
        return (
            StageStatus.IN_PROGRESS,
            f"{received}; {intake.files_requiring_review} received file(s) await review",
            "Review received package files",
            review_path,
            None,
        )
    return (
        StageStatus.COMPLETE,
        f"{received}; every received file is reviewed",
        "Import addendum or revised package",
        f"{section}/import?mode=addendum",
        None,
    )


def _documents_stage(
    bid_id: str,
    navigator: NavigatorStage | None,
    intake: PackageIntakeFacts,
) -> tuple[StageStatus, str, str, str]:
    """Derive stage 3 from the OPS-07W document count and received-package context."""
    status = _navigator_status(navigator)
    count = navigator.completeness if navigator else "0 controlled source(s)"
    section = _section(bid_id, "package-intake-addenda")
    if status is StageStatus.NOT_STARTED and intake.latest_release_id:
        # The prerequisite exists: control the files that were actually received
        # rather than sending the user to an empty register.
        return (
            StageStatus.NOT_STARTED,
            f"{count}; received files are not yet under document control",
            "Register a controlled customer document",
            f"{section}/releases/{intake.latest_release_id}",
        )
    if status is StageStatus.NOT_STARTED:
        return (
            StageStatus.NOT_STARTED,
            count,
            "Register a controlled customer document",
            f"/documents?bid_id={bid_id}#register-form",
        )
    return (status, count, "Review controlled customer documents", f"/documents?bid_id={bid_id}")


def _handover_stage(
    bid_id: str,
    readiness_verdict: str,
    bid_status: str,
    intake: PackageIntakeFacts,
    first_blocker_path: str | None,
) -> tuple[StageStatus, str, str, str, bool, str | None]:
    """Derive stage 9, which owns the Bid Basis and never advertises a download early.

    Publishing the immutable Bid Basis belongs here rather than to package intake,
    so a received release is only incorporated once its files are under document
    control.
    """
    section = _section(bid_id, "award-handover")
    intake_section = _section(bid_id, "package-intake-addenda")
    basis = "Bid Basis published" if intake.basis_published else "Bid Basis not published"
    verdict = readiness_verdict.casefold()
    if bid_status.casefold() in _PURSUIT_DECIDED:
        return (
            StageStatus.COMPLETE,
            f"{basis}; Bid outcome recorded",
            "Open Bid Handover",
            f"/bids/{bid_id}/handover",
            True,
            None,
        )
    if intake.not_incorporated:
        return (
            StageStatus.BLOCKED if verdict != "clear" else StageStatus.IN_PROGRESS,
            (
                f"{basis}; {intake.not_incorporated} received release(s) "
                "are not yet in the Bid Basis"
            ),
            "Publish or update the Bid Basis",
            f"{intake_section}#publish-basis",
            # Publishing the Bid Basis is always available; only the handover
            # download waits for a Clear readiness verdict.
            True,
            (
                None
                if verdict == "clear"
                else "Handover cannot be produced until the outstanding prerequisites are resolved."
            ),
        )
    if verdict == "clear":
        return (
            StageStatus.READY_FOR_REVIEW,
            f"{basis}; readiness is Clear",
            "Open Bid Handover",
            f"/bids/{bid_id}/handover",
            True,
            None,
        )
    return (
        StageStatus.BLOCKED,
        f"{basis}; readiness is {readiness_verdict.title()}",
        "Resolve the outstanding readiness prerequisite",
        first_blocker_path or section,
        False,
        "Handover cannot be produced until the outstanding prerequisites are resolved.",
    )


_LABELS: tuple[tuple[str, str, str], ...] = (
    # (stage key, business label, Bid workspace section)
    ("setup", "Bid setup", "overview"),
    ("package-intake", "Package intake and addenda", "package-intake-addenda"),
    ("documents", "Controlled customer documents", "package-intake-addenda"),
    ("requirements", "Requirements and responses", "requirements-scope"),
    ("scope", "Scope and interfaces", "requirements-scope"),
    ("manufacturers", "Manufacturers and supplier coverage", "manufacturers-coverage"),
    ("commercial", "Commercial, contract risk and approvals", "commercial-contract"),
    ("proposal", "Proposal and negotiation", "proposal-negotiation"),
    ("handover", "Bid Basis and handover", "award-handover"),
)

# Business-language actions for the stages OPS-07W already scores.
_NAVIGATOR_ACTIONS: dict[str, tuple[str, str]] = {
    "setup": ("Review Bid setup and classification", "/bids/{bid}/classification"),
    "requirements": (
        "Add a customer requirement and response",
        "/requirements?bid_id={bid}#create",
    ),
    "scope": ("Add a scope item or interface", "/scope-interfaces?bid_id={bid}#create-scope"),
    "manufacturers": (
        # The Bid workspace carries this form, so the user records coverage without
        # leaving the Bid for the whole-portfolio vendor-document register.
        "Add a manufacturer or supplier package",
        "/bids/{bid}/manufacturers-coverage#add-manufacturer-package",
    ),
    "commercial": ("Resolve a commercial or contract position", "/commercial?bid_id={bid}#author"),
    "proposal": ("Prepare the proposal and deliverables", "/proposals?bid_id={bid}#author"),
}

# The OPS-07W navigator key that scores each spine stage, where one exists.
_NAVIGATOR_KEY: dict[str, str] = {
    "setup": "setup",
    "requirements": "requirements",
    "scope": "scope",
    "manufacturers": "manufacturer",
    "commercial": "commercial",
    "proposal": "proposal",
}


def project_bid_workflow(
    bid_id: str,
    *,
    bid_status: str,
    navigator: Sequence[NavigatorStage],
    intake: PackageIntakeFacts,
    readiness_verdict: str,
    outstanding: Sequence[OutstandingItem],
) -> BidWorkflowProjection:
    """Build the nine-stage spine, one next action and one compact status line.

    Args:
        bid_id: The Bid whose workspace is being rendered.
        bid_status: The authoritative ``BidStatus`` value for this Bid.
        navigator: The OPS-07W navigator stages for this Bid; the only source of
            completeness for the stages it scores.
        intake: OPS-11 received-package facts for this Bid.
        readiness_verdict: The TASK-06 verdict value; never recomputed here.
        outstanding: Already-derived outstanding items, most severe first.

    Returns:
        One projection carrying the nine stages, the single next required action
        and the compact status lines.

    Raises:
        ValueError: If ``bid_id`` is empty, which would produce unusable links.
    """
    if not bid_id:
        raise ValueError("bid_id is required to build Bid-scoped workflow links")

    scored = {stage.key: stage for stage in navigator}
    stopped = bid_status.casefold() in _PURSUIT_STOPPED
    first_blocker_path = next((item.resolve_path for item in outstanding if item.blocking), None)

    built: list[WorkflowStage] = []
    for number, (key, label, section) in enumerate(_LABELS, start=1):
        section_path = _section(bid_id, section)
        blocked_reason: str | None = None
        actionable = True
        if key == "package-intake":
            status, summary, action_label, action_path, blocked_reason = _package_intake_stage(
                bid_id, intake
            )
        elif key == "documents":
            status, summary, action_label, action_path = _documents_stage(
                bid_id, scored.get("documents"), intake
            )
        elif key == "handover":
            (
                status,
                summary,
                action_label,
                action_path,
                actionable,
                blocked_reason,
            ) = _handover_stage(bid_id, readiness_verdict, bid_status, intake, first_blocker_path)
        else:
            source = scored.get(_NAVIGATOR_KEY[key])
            status = _navigator_status(source)
            summary = source.completeness if source else "Not yet recorded"
            blocked_reason = source.blocker if source else None
            action_label, template = _NAVIGATOR_ACTIONS[key]
            action_path = template.format(bid=bid_id)
        if stopped and key != "setup":
            status = StageStatus.NOT_APPLICABLE
            summary = "Pursuit stopped; this stage no longer applies"
            actionable = False
            blocked_reason = None
        built.append(
            WorkflowStage(
                key=key,
                number=number,
                label=label,
                status=status,
                summary=summary,
                action_label=action_label,
                action_path=action_path,
                section_path=section_path,
                actionable=actionable,
                blocked_reason=blocked_reason,
            )
        )

    current_index = next(
        (index for index, stage in enumerate(built) if not stage.settled),
        len(built) - 1,
    )
    # A future stage stays visible for orientation but must not advertise an
    # action before the work in front of it has begun. A stage whose prerequisites
    # already fail is still future work, so a failing later gate does not turn into
    # an invitation to act out of sequence.
    stages = tuple(
        stage
        if index <= current_index or stage.status not in _PREMATURE
        else stage.model_copy(update={"actionable": False})
        for index, stage in enumerate(built)
    )
    current = stages[current_index]
    action_stage = next(
        (stage for stage in stages if not stage.settled and stage.actionable),
        current,
    )
    return BidWorkflowProjection(
        bid_id=bid_id,
        stages=stages,
        current_stage_key=current.key,
        next_action_label=action_stage.action_label,
        next_action_path=action_stage.action_path,
        next_action_stage_label=action_stage.label,
        status_line=f"{current.label} — {current.status.value.lower()}",
        readiness_line=_readiness_line(readiness_verdict, outstanding),
        outstanding=tuple(outstanding),
    )


def _readiness_line(readiness_verdict: str, outstanding: Sequence[OutstandingItem]) -> str:
    blocking = sum(1 for item in outstanding if item.blocking)
    if not blocking:
        return f"Readiness: {readiness_verdict.title()} — no prerequisite outstanding"
    noun = "prerequisite" if blocking == 1 else "prerequisites"
    return f"Readiness: {readiness_verdict.title()} — {blocking} {noun} outstanding"


__all__ = [
    "BLOCKER_HEADINGS",
    "INTAKE_HEADINGS",
    "PROPOSAL_HEADINGS",
    "PACKAGE_NOT_IMPORTED_HEADING",
    "STAGE_COUNT",
    "BidWorkflowProjection",
    "OutstandingItem",
    "PackageIntakeFacts",
    "StageStatus",
    "WorkflowStage",
    "blocker_heading",
    "intake_heading",
    "proposal_heading",
    "project_bid_workflow",
]
