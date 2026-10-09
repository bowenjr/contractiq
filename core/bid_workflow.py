"""One presentation projection of the single Bid workflow spine.

This module creates no authority and holds no state. Every stage status, every
outstanding item and the single next required action are derived from facts that
authoritative sources have already produced:

* TASK-06 remains the only readiness engine; its verdict and blockers arrive here
  already evaluated.
* OPS-07W ``build_navigator`` remains the source of stage activity, counts and
  intrinsic blocking for setup, documents, requirements, scope, manufacturers,
  commercial and proposal; development- versus issue-phase outstanding items
  refine the displayed preparation status without changing those source facts.
* OPS-11 package intake remains the only source of received-package facts.

Nothing here reads storage, a database connection or a clock.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from core.guided_workflow import (
    GuidedTask,
    StageStatus,
    TaskState,
    project_guided_tasks,
)
from core.ops07w import NavigatorStage
from core.owner_decision_evidence import initial_requirements_assessment_available
from core.owner_decisions import OwnerDecisionEvidenceSnapshot, OwnerDecisionValue

STAGE_COUNT = 10


# Statuses that need no further work from the Bid team at this stage.
_SETTLED = frozenset({StageStatus.COMPLETE, StageStatus.COMPLETE_OPEN, StageStatus.NOT_APPLICABLE})

# Statuses which, on a stage the work has not reached yet, must not advertise an
# action. A later stage that is merely waiting on its prerequisites is not a task.
_PREMATURE = frozenset({StageStatus.NOT_STARTED, StageStatus.BLOCKED})

_NAVIGATOR_STATUS: dict[str, StageStatus] = {
    "Not started": StageStatus.NOT_STARTED,
    "In progress": StageStatus.IN_PROGRESS,
    "Blocked": StageStatus.BLOCKED,
    "Complete": StageStatus.COMPLETE,
    "Done — items open": StageStatus.COMPLETE_OPEN,
}

# Bid statuses that end pre-award pursuit work.
_PURSUIT_STOPPED = frozenset({"no_bid"})
_PURSUIT_DECIDED = frozenset({"won", "lost"})
_LEGACY_NOT_REGATED = frozenset({"submitted", "won", "lost"})


class PackageIntakeFacts(BaseModel):
    """Received-package facts read from the OPS-11 intake summary only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    releases_received: int = Field(ge=0)
    releases_incorporated: int = Field(ge=0)
    files_requiring_review: int = Field(ge=0)
    expected_missing: int = Field(ge=0)
    basis_published: bool = False
    latest_release_id: str | None = None
    governing_sources_selected: int | None = Field(default=None, ge=0)
    governing_sources_pending_control: int | None = Field(default=None, ge=0)
    included_files: int = Field(default=0, ge=0)
    discovery_known: bool = False
    discovery_active_job_id: str | None = None
    discovery_latest_job_id: str | None = None
    discovery_latest_state: str | None = None
    discovery_proposals_pending: int = Field(default=0, ge=0)
    discovery_included_unanalysed: int = Field(default=0, ge=0)
    discovery_passages_unanalysed: int = Field(default=0, ge=0)
    discovery_files_incomplete: int = Field(default=0, ge=0)
    discovery_files_not_readable: int = Field(default=0, ge=0)

    @property
    def not_incorporated(self) -> int:
        return max(self.releases_received - self.releases_incorporated, 0)


class WorkflowStage(BaseModel):
    """One stage of the ten-stage Bid workflow spine."""

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
    # Stable source identifier when the producing projection exposes one. Older
    # callers may omit it; stage reconciliation then falls back to the canonical
    # negative heading produced by ``proposal_heading``.
    code: str | None = None
    # Which part of the Bid this item belongs to: ingesting a package
    # ("package_intake"), confirming the Bid Basis after document control
    # ("bid_basis"), or later whole-Bid readiness ("whole_bid").
    scope: str = "whole_bid"
    # How many underlying records this line stands for (for example, 42 unanswered manufacturer
    # rows under one heading). Blocking totals count records, not headings.
    count: int = Field(default=1, ge=1)


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
    stopped_reason: str | None = None
    secondary_action_label: str | None = None
    secondary_action_path: str | None = None
    tasks: tuple[GuidedTask, ...] = ()
    current_task_key: str = ""

    @property
    def current_stage(self) -> WorkflowStage:
        return next(stage for stage in self.stages if stage.key == self.current_stage_key)

    @property
    def current_task(self) -> GuidedTask:
        """Return the task selected as the Bid's current guided task."""
        task = self.task(self.current_task_key)
        if task is None:
            raise ValueError("The workflow projection has no current guided task")
        return task

    def task(self, key: str) -> GuidedTask | None:
        """Return one guided task by key, or None when it is not present."""
        return next((task for task in self.tasks if task.key == key), None)

    def next_task(self, key: str) -> GuidedTask | None:
        """Return the guided task after ``key``, or None at the end."""
        for index, task in enumerate(self.tasks):
            if task.key == key:
                return self.tasks[index + 1] if index + 1 < len(self.tasks) else None
        return None

    def task_for_page(self, path: str) -> GuidedTask | None:
        """Choose the most relevant guided task for one Bid-relative page path."""
        relative = path.split("?", 1)[0].lstrip("/")
        bid_prefix = f"bids/{self.bid_id}/"
        if relative.startswith(bid_prefix):
            relative = relative[len(bid_prefix) :]
        candidates = tuple(
            task
            for task in self.tasks
            if any(relative == prefix or relative.startswith(f"{prefix}/") for prefix in task.pages)
        )
        if not candidates:
            return None
        current = next((task for task in candidates if task.key == self.current_task_key), None)
        if current is not None:
            return current
        return next((task for task in candidates if task.state.value != "Upcoming"), candidates[0])

    @property
    def blocking_count(self) -> int:
        return sum(item.count for item in self.outstanding if item.blocking)

    def blocking_count_for(self, scope: str) -> int:
        """Count blocking items belonging to one scope."""
        return sum(item.count for item in self.outstanding if item.blocking and item.scope == scope)

    def items_for(self, scope: str) -> tuple[OutstandingItem, ...]:
        """Return the outstanding items belonging to one scope, in display order."""
        return tuple(item for item in self.outstanding if item.scope == scope)


# --------------------------------------------------------------------------- #
# Blocker headings
# --------------------------------------------------------------------------- #
# The gate engine states each condition positively ("The owner has decided to
# pursue this Bid."). A heading must say what is missing, so presentation maps each
# authoritative condition id to a short negative heading. The engine, the
# condition ids and the evaluated detail text are unchanged.
BLOCKER_HEADINGS: dict[str, str] = {
    "g0.bid_complete": "Bid setup incomplete",
    "g1.owner_pursuit_decided": "Owner pursuit decision missing",
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
    "g6.senior_approvals_current": "Senior approval incomplete",
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
    "BID_STOPPED": "Bid stopped by owner decision",
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


_NOT_IN_BASIS_HEADINGS: dict[str, str] = {
    "INITIAL_PACKAGE": "Initial package not yet in Bid Basis",
    "ADDENDUM": "Addendum not incorporated",
    "CLARIFICATION": "Clarification not incorporated",
    "REVISED_PACKAGE": "Revised package not incorporated",
    "OTHER": "Customer release not incorporated",
}


def intake_heading(code: str, fallback: str, release_type: str | None = None) -> str:
    """Return the negative heading for one package-intake attention code.

    A release that is not yet in the Bid Basis is named for what it is: an
    initial package is not an addendum.
    """
    if code == "RECEIVED_RELEASE_NOT_IN_BASIS" and release_type in _NOT_IN_BASIS_HEADINGS:
        return _NOT_IN_BASIS_HEADINGS[release_type]
    return INTAKE_HEADINGS.get(code, fallback)


def proposal_heading(code: str, fallback: str) -> str:
    """Return the negative heading for one proposal-readiness blocker code."""
    return PROPOSAL_HEADINGS.get(code, fallback)


def proposal_resolve_label(destination: str, condition: str) -> str:
    """Name the concrete control reached by a proposal-blocker destination."""
    if condition == "BID_STOPPED" or "/owner-decision" in destination:
        return "Record owner decision"
    if "#authority-policies" in destination:
        return "Configure approval policy"
    if "package-intake-addenda" in destination:
        return "Review governing sources"
    if "/requirements" in destination:
        return "Review customer requirements"
    if "/manufacturers-coverage" in destination:
        return "Review manufacturer coverage"
    if "/commercial" in destination or "/decisions" in destination:
        return "Review approval requirements"
    if "/documents/" in destination:
        return "Review controlled source"
    if "/proposal-issue-control" in destination:
        return "Review proposal control"
    return "Open required Bid control"


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
    evidence: OwnerDecisionEvidenceSnapshot | None = None,
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
    missing_summary = (
        f"; {intake.expected_missing} expected release(s) not received"
        if intake.expected_missing
        else ""
    )
    n = intake.files_requiring_review
    latest = (intake.discovery_latest_state or "").upper()
    manual_assessment = evidence is not None and (
        evidence.requirement_count > 0 or evidence.accepted_deliverable_count > 0
    )
    passed = intake.discovery_active_job_id is None and (
        (intake.included_files > 0 and latest in {"SUCCEEDED", "PARTIAL"}) or manual_assessment
    )
    open_items = any(
        (
            n > 0,
            latest == "PARTIAL",
            intake.discovery_included_unanalysed > 0,
            intake.discovery_passages_unanalysed > 0,
            intake.discovery_files_incomplete > 0,
            intake.discovery_files_not_readable > 0,
        )
    )
    action = intake_discovery_action(bid_id, intake)
    if passed:
        summary = received + (f"; {n} file(s) remain undecided" if n else "")
        if action is None:
            action = (
                ("Review received package files", review_path)
                if n
                else ("Import addendum or revised package", f"{section}/import?mode=addendum")
            )
        return (
            StageStatus.COMPLETE_OPEN if open_items else StageStatus.COMPLETE,
            summary + missing_summary,
            action[0],
            action[1],
            None,
        )
    if action is not None:
        return (
            StageStatus.IN_PROGRESS,
            received
            + (f"; {n} file(s) still undecided and will not be analysed" if n else "")
            + missing_summary,
            action[0],
            action[1],
            None,
        )
    if intake.included_files == 0:
        return (
            StageStatus.IN_PROGRESS,
            f"{received}; {n} received file(s) await review; none chosen for analysis"
            + missing_summary,
            "Choose files for analysis",
            review_path,
            None,
        )
    return (
        StageStatus.IN_PROGRESS,
        received + missing_summary,
        "Run deliverables discovery",
        f"/bids/{bid_id}/deliverable-discovery",
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
    if status is StageStatus.NOT_STARTED and intake.latest_release_id:
        # The prerequisite exists: control the files that were actually received
        # rather than sending the user to an empty register.
        action_label, action_path = intake_control_action(bid_id, intake)
        return (
            StageStatus.NOT_STARTED,
            f"{count}; received files are not yet under document control",
            action_label,
            action_path,
        )
    if status is StageStatus.NOT_STARTED:
        return (
            StageStatus.NOT_STARTED,
            count,
            "Register a controlled customer document",
            f"/documents?bid_id={bid_id}#register-form",
        )
    return (status, count, "Review controlled customer documents", f"/documents?bid_id={bid_id}")


def intake_control_action(bid_id: str, intake: PackageIntakeFacts) -> tuple[str, str]:
    """Return the first usable received-package control and its visible anchor."""
    section = _section(bid_id, "package-intake-addenda")
    release = (
        f"{section}/releases/{intake.latest_release_id}" if intake.latest_release_id else section
    )
    # A zero-Included legacy intake still needs a file decision before document
    # control. Once any files are Included, undecided files are a task-F need and
    # governing-source control may proceed independently.
    if intake.files_requiring_review and intake.included_files == 0:
        return "Review received package files", f"{release}#files"
    if not intake.governing_sources_selected:
        return "Choose governing sources", f"{release}#governing-sources"
    if intake.governing_sources_pending_control:
        return "Put files under document control", f"{release}#document-control"
    return "Confirm Bid Basis", f"{section}#publish-basis"


def intake_discovery_action(bid_id: str, intake: PackageIntakeFacts) -> tuple[str, str] | None:
    """Return the first usable deliverables-discovery control, or None if none applies."""
    base = f"/bids/{bid_id}/deliverable-discovery"
    if intake.discovery_active_job_id:
        return "View discovery progress", f"{base}/jobs/{intake.discovery_active_job_id}"
    if intake.included_files == 0:
        return None
    if intake.discovery_latest_state == "PARTIAL":
        if intake.discovery_proposals_pending > 0:
            return "Review proposed deliverables", f"{base}#proposals"
        return "Review partial discovery results", f"{base}/jobs/{intake.discovery_latest_job_id}"
    if intake.discovery_latest_state == "FAILED":
        if (
            intake.discovery_included_unanalysed == 0
            and intake.discovery_files_incomplete == 0
            and intake.discovery_passages_unanalysed == 0
        ):
            package = f"/bids/{bid_id}/package-intake-addenda"
            release = (
                f"{package}/releases/{intake.latest_release_id}"
                if intake.latest_release_id
                else package
            )
            return "Choose files for analysis", f"{release}#files"
        return "Retry deliverables discovery", f"{base}/jobs/{intake.discovery_latest_job_id}"
    if intake.discovery_latest_state == "SUCCEEDED":
        if intake.discovery_proposals_pending > 0:
            return "Review proposed deliverables", f"{base}#proposals"
        if (
            intake.discovery_included_unanalysed == 0
            and intake.discovery_passages_unanalysed == 0
            and intake.discovery_files_incomplete == 0
        ):
            return None
    return "Run deliverables discovery", base


def intake_secondary_action(bid_id: str, intake: PackageIntakeFacts) -> tuple[str, str] | None:
    """Return the package-file review control while files await a decision, else None."""
    if intake.files_requiring_review > 0 and intake.latest_release_id:
        return (
            "Finish reviewing files",
            f"/bids/{bid_id}/package-intake-addenda/releases/{intake.latest_release_id}#files",
        )
    return None


def _owner_decision_stage(
    bid_id: str,
    intake: PackageIntakeFacts,
    evidence: OwnerDecisionEvidenceSnapshot | None,
    decision: OwnerDecisionValue | None,
    decision_reason: str | None,
    stopped: bool,
    not_regated: bool,
) -> tuple[StageStatus, str, str, str, str | None]:
    """Derive the assessment and pursuit checkpoint in its required order."""
    path = f"/bids/{bid_id}/owner-decision"
    discovery_path = f"/bids/{bid_id}/deliverable-discovery"
    if not_regated:
        return (
            StageStatus.NOT_APPLICABLE,
            "Existing submitted or outcome Bid is not retroactively gated",
            "Review owner decision",
            path,
            None,
        )
    if stopped or decision is OwnerDecisionValue.DO_NOT_PURSUE:
        return (
            StageStatus.BLOCKED,
            f"Pursuit stopped by the owner: {decision_reason or 'No reason recorded'}",
            "Record a new decision",
            path,
            "The Bid cannot continue while the current owner decision is Do not pursue.",
        )
    if evidence is None or not initial_requirements_assessment_available(evidence):
        return (
            StageStatus.NOT_STARTED,
            "Initial requirements assessment is not yet available",
            "Run discovery or add a requirement",
            discovery_path,
            None,
        )
    if decision is None:
        return (
            StageStatus.READY_FOR_REVIEW,
            "Initial requirements assessment is available; owner decision is not recorded",
            "Record owner decision",
            path,
            None,
        )
    return (
        StageStatus.COMPLETE,
        "Owner decided to pursue this Bid",
        "Review owner decision",
        path,
        None,
    )


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
        action_label, action_path = intake_control_action(bid_id, intake)
        return (
            StageStatus.BLOCKED if verdict != "clear" else StageStatus.IN_PROGRESS,
            (
                f"{basis}; {intake.not_incorporated} received release(s) "
                "are not yet in the Bid Basis"
            ),
            action_label,
            action_path,
            # The first incomplete intake control remains usable even while the
            # later handover download waits for the development gate check.
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
            f"{basis}; development gate check is clear",
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
    ("owner-decision", "Owner decision", "owner-decision"),
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

# Proposal-readiness and development-gate items are assigned to the stage where
# their preparation work is performed. Controlled-source work remains on the
# separately projected documents stage, and owner-decision work remains on its
# dedicated stage, so neither is duplicated here.
_PREPARATION_STAGE_CODES: dict[str, str] = {
    "g0.bid_complete": "setup",
    "g2.no_scope_gaps": "scope",
    "g2.strategy_recorded": "scope",
    "g3.suppliers_supported": "manufacturers",
    "g4.commercial_review_clear": "commercial",
    "g5.mandatory_requirements_complete": "requirements",
    "g5.no_unconfirmed_material": "proposal",
    "MISSING_REQUIREMENTS": "requirements",
    "UNRESOLVED_REQUIREMENT": "requirements",
    "MISSING_PROPOSED_RESPONSE": "requirements",
    "MISSING_SCOPE": "scope",
    "UNRESOLVED_SCOPE": "scope",
    "UNRESOLVED_INTERFACE": "scope",
    "MANUFACTURER_RESPONSE_BLOCKER": "manufacturers",
    "MISSING_MANUFACTURER_EVIDENCE": "manufacturers",
    "MANUFACTURER_EXCEPTION_UNDISPOSED": "manufacturers",
    "MISSING_COMMERCIAL_POSITION": "commercial",
    "UNRESOLVED_COMMERCIAL_POSITION": "commercial",
    "UNDISPOSED_QUALIFICATION": "commercial",
    "BLOCKING_CONTRACT_RISK": "commercial",
    "MISSING_SELECTED_PRICING_SCENARIO": "commercial",
    "MISSING_ACCOUNTABLE_OWNER": "proposal",
    "MISSING_DELIVERY_COMMITMENT": "proposal",
    "UNRESOLVED_DELIVERY_COMMITMENT": "proposal",
}

_PREPARATION_STAGE_HEADINGS: dict[str, str] = {
    PROPOSAL_HEADINGS[code]: stage
    for code, stage in _PREPARATION_STAGE_CODES.items()
    if code in PROPOSAL_HEADINGS
}


def _preparation_items_by_stage(
    outstanding: Sequence[OutstandingItem],
) -> dict[str, tuple[OutstandingItem, ...]]:
    """Map development-phase work to the six navigator-scored preparation stages.

    Stable proposal-readiness codes map requirements to ``requirements``; scope
    and interface codes to ``scope``; manufacturer codes to ``manufacturers``;
    commercial, qualification, pricing and contract-risk codes to ``commercial``;
    and proposal ownership/delivery codes to ``proposal``. Development gate G0
    maps to ``setup``; G2, G3, G4 and G5 map to their corresponding work stages.
    Issue-phase items (``pending_before_issue``) are deliberately excluded. For
    legacy proposal items without a code, the canonical ``PROPOSAL_HEADINGS``
    text supplies the same deterministic mapping.
    """
    grouped: dict[str, list[OutstandingItem]] = {}
    for item in outstanding:
        if not item.blocking or item.scope == "pending_before_issue":
            continue
        stage = _PREPARATION_STAGE_CODES.get(item.code or "") or _PREPARATION_STAGE_HEADINGS.get(
            item.heading
        )
        if stage is not None:
            grouped.setdefault(stage, []).append(item)
    return {stage: tuple(items) for stage, items in grouped.items()}


def _preparation_stage_display(
    source: NavigatorStage | None,
    items: Sequence[OutstandingItem],
) -> tuple[StageStatus, str, str | None]:
    """Reconcile navigator activity with development-phase preparation work."""
    source_status = _navigator_status(source)
    if source_status is StageStatus.BLOCKED:
        summary = (
            f"Preparation remaining: {len(items)} item(s)"
            if items
            else source.completeness
            if source
            else "Preparation blocked"
        )
        return StageStatus.BLOCKED, summary, source.blocker if source else None
    if source_status is StageStatus.NOT_STARTED:
        return StageStatus.NOT_STARTED, "Not yet recorded", None
    if items:
        return StageStatus.IN_PROGRESS, f"Preparation remaining: {len(items)} item(s)", None
    return StageStatus.COMPLETE, "Preparation complete", None


def project_bid_workflow(
    bid_id: str,
    *,
    bid_status: str,
    navigator: Sequence[NavigatorStage],
    intake: PackageIntakeFacts,
    readiness_verdict: str,
    outstanding: Sequence[OutstandingItem],
    owner_decision: OwnerDecisionValue | None = None,
    owner_decision_reason: str | None = None,
    owner_decision_evidence: OwnerDecisionEvidenceSnapshot | None = None,
    proposal_issued: bool = False,
) -> BidWorkflowProjection:
    """Build the ten-stage spine, one next action and one compact status line.

    Args:
        bid_id: The Bid whose workspace is being rendered.
        bid_status: The authoritative ``BidStatus`` value for this Bid.
        navigator: The OPS-07W navigator stages supplying activity, counts and
            intrinsic blocked status for the stages it scores.
        intake: OPS-11 received-package facts for this Bid.
        readiness_verdict: The TASK-06 verdict value; never recomputed here.
        outstanding: Already-derived outstanding items, most severe first.

    Returns:
        One projection carrying the ten stages, the single next required action
        and the compact status lines.

    Raises:
        ValueError: If ``bid_id`` is empty, which would produce unusable links.
    """
    if not bid_id:
        raise ValueError("bid_id is required to build Bid-scoped workflow links")

    scored = {stage.key: stage for stage in navigator}
    not_regated = bid_status.casefold() in _LEGACY_NOT_REGATED
    stopped = not not_regated and (
        bid_status.casefold() in _PURSUIT_STOPPED
        or owner_decision is OwnerDecisionValue.DO_NOT_PURSUE
    )
    first_blocker_path = next((item.resolve_path for item in outstanding if item.blocking), None)
    preparation_items = _preparation_items_by_stage(outstanding)
    issue_approval_count = sum(1 for item in outstanding if item.scope == "pending_before_issue")

    built: list[WorkflowStage] = []
    for number, (key, label, section) in enumerate(_LABELS, start=1):
        section_path = _section(bid_id, section)
        blocked_reason: str | None = None
        actionable = True
        if key == "package-intake":
            status, summary, action_label, action_path, blocked_reason = _package_intake_stage(
                bid_id, intake, owner_decision_evidence
            )
        elif key == "owner-decision":
            status, summary, action_label, action_path, blocked_reason = _owner_decision_stage(
                bid_id,
                intake,
                owner_decision_evidence,
                owner_decision,
                owner_decision_reason,
                stopped,
                not_regated,
            )
            if not_regated:
                actionable = False
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
            if not stopped and not not_regated:
                stage_items = preparation_items.get(key, ())
                status, summary, blocked_reason = _preparation_stage_display(source, stage_items)
            else:
                stage_items = ()
                status = _navigator_status(source)
                summary = source.completeness if source else "Not yet recorded"
                blocked_reason = source.blocker if source else None
            action_label, template = _NAVIGATOR_ACTIONS[key]
            action_path = template.format(bid=bid_id)
            if key == "commercial" and not stopped and not not_regated:
                approvals = (
                    f"Approvals required before issue: {issue_approval_count} pending "
                    "(these do not hold up drafting)"
                    if issue_approval_count
                    else "Approvals required before issue: none pending"
                )
                summary = f"{summary}; {approvals}"
                if not stage_items:
                    action_label = "Review commercial positions and approvals"
        if stopped and key not in {"setup", "package-intake", "owner-decision"}:
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

    current_index = (
        next(index for index, stage in enumerate(built) if stage.key == "owner-decision")
        if stopped
        else next(
            (index for index, stage in enumerate(built) if not stage.settled),
            len(built) - 1,
        )
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
    tasks = project_guided_tasks(
        bid_id,
        bid_status=bid_status,
        stages=stages,
        intake=intake,
        readiness_verdict=readiness_verdict,
        outstanding=outstanding,
        owner_decision=owner_decision,
        owner_decision_evidence=owner_decision_evidence,
        proposal_issued=proposal_issued,
    )
    guided_current = (
        next(task for task in tasks if task.key == "decide")
        if stopped
        else next(
            (task for task in tasks if task.state is TaskState.CURRENT),
            tasks[-1],
        )
    )
    receive_task = tasks[0]
    secondary_source = (
        receive_task
        if intake.releases_received > 0 and intake.expected_missing > 0 and receive_task.later
        else (
            guided_current
            if guided_current.later
            else next(
                (
                    task
                    for task in reversed(tasks[: guided_current.number - 1])
                    if task.state is TaskState.DONE_OPEN and task.later
                ),
                None,
            )
        )
    )
    secondary = secondary_source.later[0] if secondary_source is not None else None
    return BidWorkflowProjection(
        bid_id=bid_id,
        stages=stages,
        current_stage_key=current.key,
        next_action_label=guided_current.primary_label,
        next_action_path=guided_current.primary_path,
        next_action_stage_label=guided_current.label,
        status_line=f"{current.label} — {current.status.value.lower()}",
        readiness_line=_readiness_line(readiness_verdict, outstanding),
        outstanding=tuple(outstanding),
        stopped_reason=owner_decision_reason if stopped else None,
        secondary_action_label=secondary.action_label if secondary else None,
        secondary_action_path=secondary.action_path if secondary else None,
        tasks=tasks,
        current_task_key=guided_current.key,
    )


def _readiness_line(readiness_verdict: str, outstanding: Sequence[OutstandingItem]) -> str:
    blocking = sum(item.count for item in outstanding if item.blocking)
    if not blocking:
        return (
            f"Development gate check: {readiness_verdict.title()} "
            "(G0–G5 conditions assessed so far)"
        )
    noun = "prerequisite" if blocking == 1 else "prerequisites"
    return f"Development gate check: {readiness_verdict.title()} — {blocking} {noun} outstanding"


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
    "intake_control_action",
    "proposal_heading",
    "project_bid_workflow",
]
