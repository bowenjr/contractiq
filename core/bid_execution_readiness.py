"""Pure readiness rules and the database-facing facts builder for Bid execution."""

from __future__ import annotations

from datetime import date
from urllib.parse import quote

from core.bid_execution import PrepStatus
from core.bid_execution_approvals import (
    ApprovalApplicability,
    ApprovalRequirementFact,
    ApprovalRequirementStatus,
    read_approval_requirement_facts,
)
from core.bid_execution_dashboard import ExecutionDashboardService, ExecutionListSlug
from core.bid_execution_projection import ExecutionFilter, ExecutionItem, ExecutionListKind
from core.bid_execution_readiness_models import (
    ExecutionReadiness,
    ExecutionReadinessVerdict,
    ReadinessFacts,
    ReadinessFinding,
    ReadinessSeverity,
)
from core.bid_repository import BidRepository
from core.database import Database
from core.deliverables import DeliverableCriticality, LifecyclePhase
from core.work_items import WorkItemPriority

ADVISORY = (
    "Advisory - the Bid Manager decides. ContractIQ does not approve, submit or authorize a Bid."
)
_SEVERITY_ORDER = {
    ReadinessSeverity.BLOCKING: 0,
    ReadinessSeverity.WARNING: 1,
    ReadinessSeverity.INFO: 2,
}


def _url(bid_id: str, list_name: str, state: ExecutionFilter) -> str:
    return f"/bids/{quote(bid_id)}/execution/{list_name}?state={state.value}"


def _links(items: list[ExecutionItem]) -> list[tuple[str, str]]:
    return sorted(
        {(item.title, item.item_url) for item in items},
        key=lambda value: (value[0].casefold(), value[1]),
    )


def _finding(
    severity: ReadinessSeverity,
    code: str,
    headline: str,
    detail: str,
    items: list[ExecutionItem],
    resolve_label: str,
    resolve_url: str,
    *,
    count: int | None = None,
) -> ReadinessFinding:
    return ReadinessFinding(
        severity=severity,
        code=code,
        headline=headline,
        count=len(items) if count is None else count,
        detail=detail,
        items=_links(items),
        resolve_label=resolve_label,
        resolve_url=resolve_url,
    )


def _outstanding(item: ExecutionItem) -> bool:
    return item.status not in {PrepStatus.COMPLETE, PrepStatus.NOT_REQUIRED}


def _critical_action(item: ExecutionItem) -> bool:
    return item.kind is ExecutionListKind.ACTION and item.priority is WorkItemPriority.CRITICAL


def _high_or_critical_action(item: ExecutionItem) -> bool:
    return item.kind is ExecutionListKind.ACTION and item.priority in {
        WorkItemPriority.HIGH,
        WorkItemPriority.CRITICAL,
    }


def _add_item_findings(
    facts: ReadinessFacts, findings: list[ReadinessFinding], as_of: date
) -> None:
    mandatory = [
        item
        for item in facts.returnables
        if item.mandatory and item.required and _outstanding(item)
    ]
    if mandatory:
        findings.append(
            _finding(
                ReadinessSeverity.BLOCKING,
                "mandatory_returnables_incomplete",
                "Mandatory returnables are incomplete",
                "Complete each mandatory Bid returnable or record why it is not required.",
                mandatory,
                "Review outstanding returnables",
                _url(facts.bid_id, "returnables", ExecutionFilter.OUTSTANDING),
            )
        )

    required_outstanding = [
        item
        for item in (*facts.returnables, *facts.manufacturer_inputs, *facts.actions)
        if item.required and _outstanding(item)
    ]
    missing = [item for item in required_outstanding if item.responsible is None]
    blocking_missing = [item for item in missing if item.mandatory or _critical_action(item)]
    warning_missing = [item for item in missing if item not in blocking_missing]
    for severity, code, headline, selected in (
        (
            ReadinessSeverity.BLOCKING,
            "missing_responsible_blocking",
            "A responsible person is missing for critical work",
            blocking_missing,
        ),
        (
            ReadinessSeverity.WARNING,
            "missing_responsible_warning",
            "A responsible person is missing",
            warning_missing,
        ),
    ):
        if selected:
            findings.append(
                _finding(
                    severity,
                    code,
                    headline,
                    "Assign or confirm a responsible person; ContractIQ has not inferred one.",
                    selected,
                    "Assign or confirm owners",
                    _url(facts.bid_id, "overdue", ExecutionFilter.UNASSIGNED),
                )
            )

    overdue = [
        item
        for item in (*facts.returnables, *facts.manufacturer_inputs, *facts.actions)
        if item.internal_due_date is not None
        and item.internal_due_date < as_of
        and _outstanding(item)
    ]
    blocking_overdue = [
        item
        for item in overdue
        if (item.mandatory and item.required) or _high_or_critical_action(item)
    ]
    warning_overdue = [item for item in overdue if item not in blocking_overdue]
    for severity, code, headline, selected in (
        (
            ReadinessSeverity.BLOCKING,
            "overdue_blocking",
            "Critical work is overdue",
            blocking_overdue,
        ),
        (ReadinessSeverity.WARNING, "overdue_warning", "Other work is overdue", warning_overdue),
    ):
        if selected:
            findings.append(
                _finding(
                    severity,
                    code,
                    headline,
                    "Review the recorded due date and finish the outstanding work.",
                    selected,
                    "Review overdue items",
                    _url(facts.bid_id, "overdue", ExecutionFilter.OVERDUE),
                )
            )


def _add_manufacturer_findings(facts: ReadinessFacts, findings: list[ReadinessFinding]) -> None:
    required = [item for item in facts.manufacturer_inputs if item.required and _outstanding(item)]
    groups = (
        (
            "manufacturer_not_started",
            "Manufacturer input verification has not started",
            [item for item in required if item.status is PrepStatus.NOT_STARTED],
        ),
        (
            "manufacturer_awaiting",
            "Manufacturer response is outstanding",
            [item for item in required if item.status is PrepStatus.IN_PROGRESS],
        ),
        (
            "manufacturer_blocked",
            "Manufacturer input needs clarification or cannot be met",
            [item for item in required if item.status is PrepStatus.BLOCKED],
        ),
    )
    for code, headline, items in groups:
        if items:
            findings.append(
                _finding(
                    ReadinessSeverity.BLOCKING,
                    code,
                    headline,
                    "Resolve each required and applicable manufacturer input before review.",
                    items,
                    "Review manufacturer inputs",
                    _url(facts.bid_id, "manufacturer-inputs", ExecutionFilter.OUTSTANDING),
                )
            )
    non_required = [item for item in facts.manufacturer_inputs if not item.required]
    if non_required:
        findings.append(
            _finding(
                ReadinessSeverity.INFO,
                "manufacturer_inputs_non_required",
                "Manufacturer inputs recorded as not required",
                "These inputs are recorded for context and do not hold submission review.",
                non_required,
                "Review non-required manufacturer inputs",
                _url(facts.bid_id, "manufacturer-inputs", ExecutionFilter.NOT_REQUIRED),
            )
        )


def _add_commercial_findings(facts: ReadinessFacts, findings: list[ReadinessFinding]) -> None:
    unresolved = [
        item
        for item in facts.commercial
        if item.status_label == "Clarification" and _outstanding(item)
    ]
    with_term = [item for item in unresolved if item.has_customer_term]
    without_term = [item for item in unresolved if not item.has_customer_term]
    for severity, code, headline, items in (
        (
            ReadinessSeverity.BLOCKING,
            "customer_term_clarification",
            "A customer-term clarification is unresolved",
            with_term,
        ),
        (
            ReadinessSeverity.WARNING,
            "clarification_without_customer_term",
            "A clarification is unresolved",
            without_term,
        ),
    ):
        if items:
            findings.append(
                _finding(
                    severity,
                    code,
                    headline,
                    "Record the customer's answer and resolve the commercial position.",
                    items,
                    "Review unresolved clarifications",
                    _url(facts.bid_id, "commercial", ExecutionFilter.CLARIFICATION),
                )
            )
    decisions = [item for item in facts.commercial if item.status_label == "Decision needed"]
    if decisions:
        findings.append(
            _finding(
                ReadinessSeverity.WARNING,
                "commercial_decision_needed",
                "A commercial or contractual decision is needed",
                "Record the controlled decision and any explicitly required approval.",
                decisions,
                "Review commercial decisions",
                _url(facts.bid_id, "commercial", ExecutionFilter.DECISION),
            )
        )
    resolved = [item for item in facts.commercial if item.status_label == "Clarification resolved"]
    if resolved:
        findings.append(
            _finding(
                ReadinessSeverity.INFO,
                "clarifications_resolved",
                "Clarifications have been resolved",
                "Resolved clarifications are recorded for context and are not outstanding.",
                resolved,
                "Review commercial positions",
                _url(facts.bid_id, "commercial", ExecutionFilter.ALL),
            )
        )


def _incomplete_prerequisites(item: ExecutionItem) -> list[tuple[str, str]]:
    return [
        (link.title, link.item_url)
        for link in item.prerequisites
        if link.status_label != "Complete" and not link.status_label.startswith("Not required")
    ]


def _add_blocker_findings(facts: ReadinessFacts, findings: list[ReadinessFinding]) -> None:
    blocked_actions = [
        item for item in facts.actions if item.status is PrepStatus.BLOCKED and _outstanding(item)
    ]
    critical_blocked = [item for item in blocked_actions if _high_or_critical_action(item)]
    other_blocked = [item for item in blocked_actions if item not in critical_blocked]
    for severity, code, headline, items in (
        (
            ReadinessSeverity.BLOCKING,
            "critical_action_blocked",
            "Critical action is blocked",
            critical_blocked,
        ),
        (ReadinessSeverity.WARNING, "action_blocked", "An action is blocked", other_blocked),
    ):
        if items:
            findings.append(
                _finding(
                    severity,
                    code,
                    headline,
                    "Resolve the recorded blocker before completing the action.",
                    items,
                    "Review blocked actions",
                    _url(facts.bid_id, "actions", ExecutionFilter.BLOCKED),
                )
            )

    waiting = [
        item
        for item in (*facts.returnables, *facts.manufacturer_inputs, *facts.actions)
        if item.waiting_on_prerequisite and _outstanding(item)
    ]
    critical_waiting = [
        item for item in waiting if (item.mandatory and item.required) or _critical_action(item)
    ]
    other_waiting = [item for item in waiting if item not in critical_waiting]
    for severity, code, items in (
        (ReadinessSeverity.BLOCKING, "critical_prerequisite_incomplete", critical_waiting),
        (ReadinessSeverity.WARNING, "prerequisite_incomplete", other_waiting),
    ):
        if not items:
            continue
        relationships = sorted(
            f"{item.title} is waiting on {title}"
            for item in items
            for title, _item_url in _incomplete_prerequisites(item)
        )
        linked_items = _links(items)
        prerequisite_links = sorted(
            {prerequisite for item in items for prerequisite in _incomplete_prerequisites(item)},
            key=lambda value: (value[0].casefold(), value[1]),
        )
        findings.append(
            ReadinessFinding(
                severity=severity,
                code=code,
                headline="Waiting on a prerequisite",
                count=len(relationships),
                detail="; ".join(relationships) + ".",
                items=sorted(
                    set((*linked_items, *prerequisite_links)),
                    key=lambda value: (value[0].casefold(), value[1]),
                ),
                resolve_label="Review waiting items",
                resolve_url=_url(facts.bid_id, "overdue", ExecutionFilter.WAITING),
            )
        )


def _add_approval_and_information(facts: ReadinessFacts, findings: list[ReadinessFinding]) -> None:
    for approval in facts.approvals:
        severity = (
            ReadinessSeverity.BLOCKING
            if approval.status is ApprovalRequirementStatus.MISSING
            and approval.classification
            in {
                ApprovalApplicability.DEVIATION_APPROVAL_REQUIRED,
                ApprovalApplicability.ROUTINE_AUTHORIZATION_REQUIRED,
            }
            else ReadinessSeverity.WARNING
            if approval.classification is ApprovalApplicability.NEEDS_ASSESSMENT
            else ReadinessSeverity.INFO
        )
        findings.append(
            ReadinessFinding(
                severity=severity,
                code=f"approval:{approval.requirement_key}",
                headline=approval.heading,
                count=1,
                detail=approval.detail,
                resolve_label=approval.resolve_label,
                resolve_url=approval.resolve_url,
            )
        )
    for code, headline, detail, count, url in (
        (
            "post_award_obligations",
            "Post-award obligations are recorded",
            "These obligations do not hold submission review by themselves.",
            facts.post_award_deliverable_count,
            f"/deliverables?bid_id={quote(facts.bid_id)}",
        ),
        (
            "optional_returnables",
            "Optional or inactive returnables are recorded",
            "These returnables do not hold submission review by themselves.",
            facts.non_blocking_returnable_count,
            _url(facts.bid_id, "returnables", ExecutionFilter.ALL),
        ),
    ):
        if count:
            findings.append(
                _finding(
                    ReadinessSeverity.INFO,
                    code,
                    headline,
                    detail,
                    [],
                    "Review recorded items",
                    url,
                    count=count,
                )
            )


def evaluate_submission_readiness(facts: ReadinessFacts, as_of: date) -> ExecutionReadiness:
    """Apply submission-readiness rules without reading a clock, database, or LLM."""
    findings: list[ReadinessFinding] = []
    _add_item_findings(facts, findings, as_of)
    _add_manufacturer_findings(facts, findings)
    _add_commercial_findings(facts, findings)
    _add_blocker_findings(facts, findings)
    _add_approval_and_information(facts, findings)
    findings.sort(
        key=lambda item: (_SEVERITY_ORDER[item.severity], item.headline.casefold(), item.code)
    )
    counts = {
        severity: sum(item.count for item in findings if item.severity is severity)
        for severity in ReadinessSeverity
    }
    verdict = (
        ExecutionReadinessVerdict.NOT_READY
        if counts[ReadinessSeverity.BLOCKING]
        else ExecutionReadinessVerdict.READY_WITH_WARNINGS
        if counts[ReadinessSeverity.WARNING]
        else ExecutionReadinessVerdict.READY_FOR_REVIEW
    )
    return ExecutionReadiness(
        bid_id=facts.bid_id,
        verdict=verdict,
        findings=findings,
        blocking_count=counts[ReadinessSeverity.BLOCKING],
        warning_count=counts[ReadinessSeverity.WARNING],
        info_count=counts[ReadinessSeverity.INFO],
        approvals=facts.approvals,
        advisory=ADVISORY,
        as_of=as_of,
    )


class BidExecutionReadinessService:
    """Gather authoritative projected facts, then call the pure evaluator."""

    def __init__(
        self,
        dashboard: ExecutionDashboardService,
        bid_repository: BidRepository,
        db: Database,
    ) -> None:
        self.dashboard = dashboard
        self.bids = bid_repository
        self.db = db

    def evaluate(self, bid_id: str, as_of: date) -> ExecutionReadiness:
        """Evaluate one Bid using dashboard-reconciling lists and existing approvals."""
        return evaluate_submission_readiness(self.facts(bid_id, as_of), as_of)

    def facts(self, bid_id: str, as_of: date) -> ReadinessFacts:
        """Build immutable readiness facts without changing any authoritative register."""
        returnables = self.dashboard.filtered_list(
            bid_id, ExecutionListSlug.RETURNABLES, ExecutionFilter.ALL, as_of
        )
        manufacturer = self.dashboard.filtered_list(
            bid_id, ExecutionListSlug.MANUFACTURER_INPUTS, ExecutionFilter.ALL, as_of
        )
        manufacturer_not_required = self.dashboard.filtered_list(
            bid_id,
            ExecutionListSlug.MANUFACTURER_INPUTS,
            ExecutionFilter.NOT_REQUIRED,
            as_of,
        )
        manufacturer_by_id = {
            item.subject_id: item for item in (*manufacturer, *manufacturer_not_required)
        }
        actions = self.dashboard.filtered_list(
            bid_id, ExecutionListSlug.ACTIONS, ExecutionFilter.ALL, as_of
        )
        commercial = self.dashboard.filtered_list(
            bid_id, ExecutionListSlug.COMMERCIAL, ExecutionFilter.ALL, as_of
        )
        deliverables = self.dashboard.deliverables.list(bid_id)
        post_award_count = sum(
            LifecyclePhase(str(row["lifecycle_phase"])) is LifecyclePhase.POST_AWARD
            for row in deliverables
        )
        optional_count = sum(
            LifecyclePhase(str(row["lifecycle_phase"]))
            in {LifecyclePhase.WITH_BID, LifecyclePhase.BOTH}
            and (
                DeliverableCriticality(str(row["criticality"]))
                is not DeliverableCriticality.MANDATORY
                or not bool(row["condition_active"])
            )
            for row in deliverables
        )
        approvals = read_approval_requirement_facts(
            self.bids,
            self.db,
            bid_id,
            as_of=as_of,
        )
        return ReadinessFacts(
            bid_id=bid_id,
            returnables=returnables,
            manufacturer_inputs=list(manufacturer_by_id.values()),
            actions=actions,
            commercial=commercial,
            post_award_deliverable_count=post_award_count,
            non_blocking_returnable_count=optional_count,
            approvals=approvals,
        )


__all__ = [
    "ADVISORY",
    "ApprovalRequirementFact",
    "BidExecutionReadinessService",
    "ExecutionReadiness",
    "ExecutionReadinessVerdict",
    "ReadinessFacts",
    "ReadinessFinding",
    "ReadinessSeverity",
    "evaluate_submission_readiness",
]
