"""Pure six-task projection over the existing Bid workflow spine."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from core.owner_decision_evidence import initial_requirements_assessment_available
from core.owner_decisions import OwnerDecisionEvidenceSnapshot, OwnerDecisionValue


class StageStatus(StrEnum):
    """The complete status vocabulary for the existing ten-stage spine."""

    NOT_STARTED = "Not started"
    IN_PROGRESS = "In progress"
    BLOCKED = "Blocked"
    READY_FOR_REVIEW = "Ready for review"
    COMPLETE = "Complete"
    COMPLETE_OPEN = "Done — items open"
    NOT_APPLICABLE = "Not applicable"

    @property
    def slug(self) -> str:
        """Return a CSS-safe token for this status."""
        return self.name.lower().replace("_", "-")


class TaskState(StrEnum):
    """Presentation state of one guided task."""

    DONE = "Done"
    DONE_OPEN = "Done — items open"
    CURRENT = "Current"
    UPCOMING = "Upcoming"
    STOPPED = "Not applicable"

    @property
    def slug(self) -> str:
        """Return a CSS-safe token for this state."""
        return self.name.lower().replace("_", "-")


class TaskNeed(BaseModel):
    """One essential condition for passing a guided task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    met: bool
    action_label: str | None
    action_path: str | None


class TaskLater(BaseModel):
    """One visible item that may be completed after a task is passed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    action_label: str
    action_path: str


class GuidedStep(BaseModel):
    """One existing spine stage shown as a sub-step of task E."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    status: StageStatus
    summary: str
    action_label: str
    action_path: str


TaskKey = Literal["receive", "analyse", "review", "decide", "prepare", "issue"]
TaskLetter = Literal["A", "B", "C", "D", "E", "F"]


class GuidedTask(BaseModel):
    """One of the six business tasks guiding a Bid from receipt to issue."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    number: int = Field(ge=1, le=6)
    key: TaskKey
    letter: TaskLetter
    label: str
    objective: str
    state: TaskState
    summary: str
    needs: tuple[TaskNeed, ...]
    later: tuple[TaskLater, ...]
    steps: tuple[GuidedStep, ...]
    primary_label: str
    primary_path: str
    continue_label: str | None
    continue_path: str | None
    alternative_label: str | None
    alternative_path: str | None
    pages: tuple[str, ...]


class _Intake(Protocol):
    releases_received: int
    releases_incorporated: int
    files_requiring_review: int
    expected_missing: int
    basis_published: bool
    latest_release_id: str | None
    governing_sources_selected: int | None
    governing_sources_pending_control: int | None
    included_files: int
    discovery_active_job_id: str | None
    discovery_latest_job_id: str | None
    discovery_latest_state: str | None
    discovery_proposals_pending: int
    discovery_included_unanalysed: int
    discovery_passages_unanalysed: int
    discovery_files_incomplete: int
    discovery_files_not_readable: int

    @property
    def not_incorporated(self) -> int: ...


class _Stage(Protocol):
    key: str
    label: str
    status: StageStatus
    summary: str
    action_label: str
    action_path: str


class _Outstanding(Protocol):
    heading: str
    resolve_label: str
    resolve_path: str
    blocking: bool
    scope: str


_PAGES: tuple[tuple[str, ...], ...] = (
    ("package-intake-addenda/import",),
    ("package-intake-addenda", "deliverable-discovery"),
    ("deliverable-discovery", "requirements"),
    ("owner-decision",),
    (
        "requirements-scope",
        "manufacturers-coverage",
        "commercial-contract",
        "proposal-negotiation",
        "requirements",
        "scope-interfaces",
        "vendor-documents",
        "commercial",
        "proposals",
        "documents",
        "suppliers",
        "commercial-qualifications",
        "contract-risks",
        "commercial-scenarios",
        "negotiations",
        "deliverables",
        "scope-items",
        "interfaces",
        "supplier-detail",
        "supplier-requests",
        "supplier-responses",
    ),
    (
        "package-intake-addenda",
        "decisions",
        "proposal-issue-control",
        "award-handover",
        "handover",
        "bid-basis-register",
    ),
)


def _release_path(bid_id: str, intake: _Intake) -> str:
    base = f"/bids/{bid_id}/package-intake-addenda"
    return f"{base}/releases/{intake.latest_release_id}" if intake.latest_release_id else base


def undecided_files_action(bid_id: str, intake: _Intake) -> tuple[str, str]:
    """Return the task-F action for deciding received-file use."""
    return "Review received package files", f"{_release_path(bid_id, intake)}#files"


def _state(passed: bool, open_items: bool = False) -> TaskState:
    if not passed:
        return TaskState.CURRENT
    return TaskState.DONE_OPEN if open_items else TaskState.DONE


def _need(text: str, met: bool, action: tuple[str, str]) -> TaskNeed:
    return TaskNeed(
        text=text,
        met=met,
        action_label=None if met else action[0],
        action_path=None if met else action[1],
    )


def _sentence(text: str) -> str:
    return text.rstrip(". ") + "."


def _basis_action(bid_id: str, intake: _Intake) -> tuple[str, str]:
    release = _release_path(bid_id, intake)
    if not intake.governing_sources_selected:
        return "Choose governing sources", f"{release}#governing-sources"
    if intake.governing_sources_pending_control:
        return "Put files under document control", f"{release}#document-control"
    return "Confirm Bid Basis", f"/bids/{bid_id}/package-intake-addenda#publish-basis"


def _task(
    number: int,
    key: TaskKey,
    letter: TaskLetter,
    label: str,
    objective: str,
    state: TaskState,
    summary: str,
    needs: Sequence[TaskNeed],
    primary: tuple[str, str],
    *,
    later: Sequence[TaskLater] = (),
    steps: Sequence[GuidedStep] = (),
    alternative: tuple[str, str] | None = None,
) -> GuidedTask:
    return GuidedTask(
        number=number,
        key=key,
        letter=letter,
        label=label,
        objective=objective,
        state=state,
        summary=summary,
        needs=tuple(needs),
        later=tuple(later),
        steps=tuple(steps),
        primary_label=primary[0],
        primary_path=primary[1],
        continue_label=None,
        continue_path=None,
        alternative_label=alternative[0] if alternative else None,
        alternative_path=alternative[1] if alternative else None,
        pages=_PAGES[number - 1],
    )


def _resolve_label(item: _Outstanding) -> str:
    """Name the specific outstanding item instead of the generic "Resolve in this Bid"."""
    if item.resolve_label == "Resolve in this Bid":
        return f"Resolve: {item.heading}"
    return item.resolve_label


def project_guided_tasks(
    bid_id: str,
    *,
    bid_status: str,
    stages: Sequence[_Stage],
    intake: _Intake,
    readiness_verdict: str,
    outstanding: Sequence[_Outstanding],
    owner_decision: OwnerDecisionValue | None,
    owner_decision_evidence: OwnerDecisionEvidenceSnapshot | None,
    proposal_issued: bool = False,
) -> tuple[GuidedTask, ...]:
    """Project the six guided tasks from already-authoritative workflow facts."""
    if not bid_id:
        raise ValueError("bid_id is required to build guided workflow links")
    by_key = {stage.key: stage for stage in stages}
    required_steps = (
        "documents",
        "requirements",
        "scope",
        "manufacturers",
        "commercial",
        "proposal",
    )
    expected_stage_keys = {
        "setup",
        "package-intake",
        "owner-decision",
        *required_steps,
        "handover",
    }
    if len(stages) != 10 or set(by_key) != expected_stage_keys:
        raise ValueError("Guided workflow requires all ten workflow spine stages")
    missing = [key for key in required_steps if key not in by_key]
    if missing:
        raise ValueError(f"Guided workflow requires spine stage(s): {', '.join(missing)}")

    package = f"/bids/{bid_id}/package-intake-addenda"
    discovery = f"/bids/{bid_id}/deliverable-discovery"
    requirements = f"/requirements?bid_id={bid_id}"
    release = _release_path(bid_id, intake)
    evidence = owner_decision_evidence
    assessment = evidence is not None and initial_requirements_assessment_available(evidence)
    manual = evidence is not None and (
        evidence.requirement_count > 0 or evidence.accepted_deliverable_count > 0
    )
    latest = (intake.discovery_latest_state or "").upper()

    receive_passed = intake.releases_received >= 1
    receive_action = (
        "Import the expected customer release"
        if intake.expected_missing
        else "Import customer bid package",
        f"{package}/import?mode={'addendum' if intake.releases_received else 'initial'}",
    )
    a = _task(
        1,
        "receive",
        "A",
        "Receive customer package",
        "Get the customer's files into the Bid.",
        _state(receive_passed, open_items=intake.expected_missing > 0),
        "The customer package is received."
        if receive_passed
        else "The customer package still needs to be received.",
        (
            _need(
                "Receive the customer package.",
                receive_passed,
                receive_action,
            ),
        ),
        receive_action,
        later=(
            TaskLater(
                text=(
                    "Receive the expected customer release "
                    f"({intake.expected_missing} not yet received)."
                ),
                action_label="Import the expected customer release",
                action_path=f"{package}/import?mode=addendum",
            ),
        )
        if intake.expected_missing and intake.releases_received > 0
        else (),
    )

    analysis_passed = intake.discovery_active_job_id is None and (
        (intake.included_files > 0 and latest in {"SUCCEEDED", "PARTIAL"}) or manual
    )
    analysis_open = any(
        (
            intake.files_requiring_review > 0,
            latest == "PARTIAL",
            intake.discovery_included_unanalysed > 0,
            intake.discovery_passages_unanalysed > 0,
            intake.discovery_files_incomplete > 0,
            intake.discovery_files_not_readable > 0,
        )
    )
    if intake.discovery_active_job_id:
        analyse_primary = (
            "View discovery progress",
            f"{discovery}/jobs/{intake.discovery_active_job_id}",
        )
    elif intake.included_files == 0:
        analyse_primary = ("Choose files for analysis", f"{release}#files")
    elif latest == "FAILED":
        if (
            intake.discovery_included_unanalysed == 0
            and intake.discovery_files_incomplete == 0
            and intake.discovery_passages_unanalysed == 0
        ):
            analyse_primary = ("Choose files for analysis", f"{release}#files")
        else:
            analyse_primary = (
                "Retry deliverables discovery",
                f"{discovery}/jobs/{intake.discovery_latest_job_id}",
            )
    elif intake.discovery_proposals_pending:
        analyse_primary = ("Review proposed deliverables", f"{discovery}#proposals")
    elif latest == "PARTIAL":
        analyse_primary = (
            "Review partial discovery results",
            f"{discovery}/jobs/{intake.discovery_latest_job_id}",
        )
    else:
        analyse_primary = ("Run deliverables discovery", discovery)
    analysis_later: list[TaskLater] = []
    if intake.files_requiring_review:
        analysis_later.append(
            TaskLater(
                text=f"Decide how {intake.files_requiring_review} received file(s) will be used.",
                action_label=f"Review {intake.files_requiring_review} undecided file(s)",
                action_path=f"{release}#files",
            )
        )
    if (
        intake.discovery_included_unanalysed
        or intake.discovery_passages_unanalysed
        or intake.discovery_files_incomplete
    ):
        analysis_later.append(
            TaskLater(
                text="Some Included files were not fully analysed; retry or review the findings.",
                action_label="Review and retry incomplete analysis",
                action_path=(
                    f"{discovery}/jobs/{intake.discovery_latest_job_id}"
                    if intake.discovery_latest_job_id
                    else discovery
                ),
            )
        )
    if intake.discovery_files_not_readable:
        analysis_later.append(
            TaskLater(
                text=(
                    f"{intake.discovery_files_not_readable} Included file(s) could not be read "
                    "by analysis (unsupported type or no readable text)."
                ),
                action_label="See which files",
                action_path=(
                    f"{discovery}/jobs/{intake.discovery_latest_job_id}"
                    if intake.discovery_latest_job_id
                    else discovery
                ),
            )
        )
    b = _task(
        2,
        "analyse",
        "B",
        "Choose and analyse files",
        "Choose readable files and run deliverables discovery.",
        _state(analysis_passed, analysis_open),
        "Deliverables discovery or a manual assessment is available."
        if analysis_passed
        else "Choose files and complete an initial deliverables discovery run.",
        (
            _need(
                "Include a file for analysis or record initial requirements manually.",
                intake.included_files > 0 or manual,
                ("Choose files for analysis", f"{release}#files"),
            ),
            _need(
                "Run deliverables discovery on the Included files and let it finish.",
                analysis_passed,
                analyse_primary,
            ),
        ),
        analyse_primary,
        later=analysis_later,
        alternative=("Add requirements manually instead", f"{requirements}#create")
        if intake.included_files == 0 or latest == "FAILED"
        else None,
    )

    decision_recorded = owner_decision is not None
    review_passed = decision_recorded or (assessment and intake.discovery_proposals_pending == 0)
    review_open = review_passed and (
        intake.discovery_proposals_pending > 0
        or evidence is None
        or (evidence.requirement_count == 0 and evidence.accepted_deliverable_count == 0)
    )
    review_primary = (
        (
            f"Review {intake.discovery_proposals_pending} proposed deliverable(s)",
            f"{discovery}#proposals",
        )
        if intake.discovery_proposals_pending
        else ("Review initial requirements", requirements)
    )
    review_later: list[TaskLater] = []
    if review_passed and intake.discovery_proposals_pending:
        review_later.append(
            TaskLater(
                text="Review the remaining proposed deliverables.",
                action_label=review_primary[0],
                action_path=review_primary[1],
            )
        )
    if review_passed and (
        evidence is None
        or (evidence.requirement_count == 0 and evidence.accepted_deliverable_count == 0)
    ):
        review_later.append(
            TaskLater(
                text="Record an initial customer requirement.",
                action_label="Add a customer requirement",
                action_path=f"{requirements}#create",
            )
        )
    c = _task(
        3,
        "review",
        "C",
        "Review findings and set initial requirements",
        "Review proposed deliverables and record the initial requirements position.",
        _state(review_passed, review_open),
        "The initial requirements position can support the owner decision."
        if review_passed
        else "Review proposed deliverables before the owner decision.",
        (
            _need(
                "Make an initial requirements assessment available.",
                assessment or decision_recorded,
                review_primary,
            ),
            _need(
                "Review every pending proposed deliverable.",
                intake.discovery_proposals_pending == 0 or decision_recorded,
                review_primary,
            ),
        ),
        review_primary,
        later=review_later,
    )

    stopped = (
        bid_status.casefold() == "no_bid" or owner_decision is OwnerDecisionValue.DO_NOT_PURSUE
    )
    pursue = owner_decision is OwnerDecisionValue.PURSUE
    decision_primary = (
        "Record a new decision" if stopped else "Record owner decision",
        f"/bids/{bid_id}/owner-decision",
    )
    d = _task(
        4,
        "decide",
        "D",
        "Record the owner's pursuit decision",
        "Record whether the owner will pursue the Bid.",
        TaskState.CURRENT if stopped else _state(pursue),
        "The Bid is stopped by the owner's current decision."
        if stopped
        else (
            "The owner decided to pursue the Bid."
            if pursue
            else "The owner's pursuit decision is not recorded."
        ),
        (_need("Record a Pursue decision.", pursue, decision_primary),),
        decision_primary,
    )

    whole_bid = tuple(item for item in outstanding if item.blocking and item.scope == "whole_bid")
    issue_items = tuple(item for item in outstanding if item.scope == "pending_before_issue")
    steps = tuple(
        GuidedStep(
            key=by_key[key].key,
            label=by_key[key].label,
            status=by_key[key].status,
            summary=by_key[key].summary,
            action_label=by_key[key].action_label,
            action_path=by_key[key].action_path,
        )
        for key in required_steps
    )
    unsettled = next(
        (
            step
            for step in steps
            if step.status
            not in {StageStatus.COMPLETE, StageStatus.COMPLETE_OPEN, StageStatus.NOT_APPLICABLE}
        ),
        steps[-1],
    )
    prepare_primary = (
        (_resolve_label(whole_bid[0]), whole_bid[0].resolve_path)
        if whole_bid
        else (unsettled.action_label, unsettled.action_path)
    )
    prepare_passed = readiness_verdict.casefold() == "clear" and not whole_bid
    e = _task(
        5,
        "prepare",
        "E",
        "Prepare the Bid",
        "Prepare scope, suppliers, commercial positions, controlled sources and the proposal.",
        _state(prepare_passed, bool(issue_items)),
        "Bid preparation is clear for issue controls."
        if prepare_passed
        else "Complete the development-gate work that is still open.",
        tuple(
            _need(_sentence(item.heading), False, ("Resolve", item.resolve_path))
            for item in whole_bid
        )
        + (
            ()
            if whole_bid
            else (
                _need(
                    "Clear the development-gate check.",
                    readiness_verdict.casefold() == "clear",
                    prepare_primary,
                ),
            )
        ),
        prepare_primary,
        later=tuple(
            TaskLater(
                text=_sentence(item.heading),
                action_label=_resolve_label(item),
                action_path=item.resolve_path,
            )
            for item in issue_items
        ),
        steps=steps,
    )

    undecided_action = undecided_files_action(bid_id, intake)
    approval_action = (
        (_resolve_label(issue_items[0]), issue_items[0].resolve_path)
        if issue_items
        else ("Review required approvals", f"/bids/{bid_id}/proposal-issue-control")
    )
    issued = proposal_issued or bid_status.casefold() in {"submitted", "won", "lost"}
    issue_needs = (
        _need(
            "Receive every release known to be missing.",
            intake.expected_missing == 0,
            (
                "Import the expected customer release",
                f"{package}/import?mode=addendum",
            ),
        ),
        _need(
            "Decide how every received file will be used.",
            intake.files_requiring_review == 0,
            undecided_action,
        ),
        _need(
            "Publish a Bid Basis containing every received release.",
            intake.basis_published and intake.not_incorporated == 0,
            _basis_action(bid_id, intake),
        ),
        _need("Obtain every approval required before issue.", not issue_items, approval_action),
        _need(
            "Issue the proposal to the customer.",
            issued,
            ("Issue the proposal", f"/bids/{bid_id}/proposal-issue-control"),
        ),
    )
    first_unmet = next((need for need in issue_needs if not need.met), issue_needs[-1])
    issue_primary = (
        first_unmet.action_label or "Review issue control",
        first_unmet.action_path or f"/bids/{bid_id}/proposal-issue-control",
    )
    f = _task(
        6,
        "issue",
        "F",
        "Confirm Bid Basis and approvals, then issue",
        "Confirm the Bid Basis and approvals, then issue the proposal.",
        _state(issued),
        "The proposal has been issued."
        if issued
        else "Complete the first remaining issue control.",
        issue_needs,
        issue_primary,
    )

    tasks = [a, b, c, d, e, f]
    if bid_status.casefold() in {"submitted", "won", "lost"}:
        tasks = [task.model_copy(update={"state": TaskState.DONE}) for task in tasks]
        current_index = len(tasks) - 1
    elif stopped:
        tasks[4] = tasks[4].model_copy(update={"state": TaskState.STOPPED})
        tasks[5] = tasks[5].model_copy(update={"state": TaskState.STOPPED})
        current_index = 3
    else:
        current_index = next(
            (
                index
                for index, task in enumerate(tasks)
                if task.state not in {TaskState.DONE, TaskState.DONE_OPEN, TaskState.STOPPED}
            ),
            len(tasks) - 1,
        )
        tasks = [
            task
            if index <= current_index
            else task.model_copy(update={"state": TaskState.UPCOMING})
            for index, task in enumerate(tasks)
        ]

    for index, task in enumerate(tasks):
        passed = task.state in {TaskState.DONE, TaskState.DONE_OPEN}
        following = next(
            (
                candidate
                for candidate in tasks[index + 1 :]
                if candidate.state not in {TaskState.DONE, TaskState.DONE_OPEN, TaskState.STOPPED}
            ),
            None,
        )
        if (passed or task.key == "review") and following is not None:
            tasks[index] = task.model_copy(
                update={
                    "continue_label": f"Continue to {following.label}",
                    "continue_path": following.primary_path,
                }
            )
    return tuple(tasks)


__all__ = [
    "GuidedStep",
    "GuidedTask",
    "StageStatus",
    "TaskLater",
    "TaskNeed",
    "TaskState",
    "project_guided_tasks",
    "undecided_files_action",
]
