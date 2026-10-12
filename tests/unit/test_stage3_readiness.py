"""Unit coverage for deterministic Stage 3 submission readiness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from core.bid_execution import ExecutionSubjectKind, PrepStatus
from core.bid_execution_dashboard import ExecutionDashboardService
from core.bid_execution_projection import (
    ExecutionFilter,
    ExecutionItem,
    ExecutionItemLink,
    ExecutionListKind,
    ExecutionProjectionService,
)
from core.bid_execution_readiness import (
    ADVISORY,
    BidExecutionReadinessService,
    ExecutionReadinessVerdict,
    ReadinessFacts,
    ReadinessSeverity,
    evaluate_submission_readiness,
)
from core.bid_execution_repository import BidExecutionRepository
from core.bid_execution_service import BidExecutionService
from core.bid_execution_subjects import ExecutionSubjectResolver
from core.bid_execution_trace import BidExecutionTrace
from core.bid_repository import BidRepository
from core.database import Database
from core.deliverable_repository import DeliverableRepository
from core.deliverables import (
    Deliverable,
    DeliverableCriticality,
    DeliverableDirection,
    DeliverableState,
    LifecyclePhase,
)
from core.document_repository import DocumentRepository
from core.enums import BidLevel
from core.ops08 import NegotiationState, Ops08Repository, PositionDisposition, PositionRevision
from core.schemas import Bid, Provenance
from core.vendor_document_control import CustomerRequirementCreate, SupplierPackageCreate
from core.vendor_document_repository import VendorDocumentRepository
from core.vendor_document_service import VendorDocumentService
from core.work_item_repository import WorkItemRepository
from core.work_item_service import WorkItemService
from core.work_items import WorkItemPriority

NOW = datetime(2026, 7, 10, 14, 30, tzinfo=UTC)
AS_OF = date(2026, 7, 10)


def _item(
    title: str,
    *,
    kind: ExecutionListKind = ExecutionListKind.ACTION,
    status: PrepStatus = PrepStatus.NOT_STARTED,
    responsible: str | None = "Bid lead",
    priority: WorkItemPriority | None = None,
    mandatory: bool = False,
    required: bool = True,
    overdue: bool = False,
    waiting: bool = False,
    prerequisites: list[ExecutionItemLink] | None = None,
    status_label: str | None = None,
    has_customer_term: bool = False,
) -> ExecutionItem:
    subject = title.lower().replace(" ", "-")
    return ExecutionItem(
        kind=kind,
        subject_id=f"internal-{subject}",
        bid_id="BID-READINESS",
        title=title,
        description=f"Prepare {title}",
        responsible=responsible,
        responsible_origin="assigned" if responsible else None,
        status=status,
        status_label=status_label or status.label,
        priority=priority,
        mandatory=mandatory,
        required=required,
        critical=mandatory or priority in {WorkItemPriority.HIGH, WorkItemPriority.CRITICAL},
        internal_due_date=date(2026, 7, 9) if overdue else None,
        overdue=overdue,
        unassigned=responsible is None,
        waiting_on_prerequisite=waiting,
        prerequisites=prerequisites or [],
        has_customer_term=has_customer_term,
        last_updated=NOW,
        item_url=f"/items/{subject}",
        origin="Added manually",
    )


def _report(**values: object):  # type: ignore[no-untyped-def]
    return evaluate_submission_readiness(ReadinessFacts(bid_id="BID-READINESS", **values), AS_OF)


@pytest.mark.parametrize(
    ("facts", "severity", "code"),
    [
        (
            {"returnables": [_item("Bid form", kind=ExecutionListKind.RETURNABLE, mandatory=True)]},
            ReadinessSeverity.BLOCKING,
            "mandatory_returnables_incomplete",
        ),
        (
            {
                "actions": [
                    _item(
                        "Critical review",
                        responsible=None,
                        priority=WorkItemPriority.CRITICAL,
                    )
                ]
            },
            ReadinessSeverity.BLOCKING,
            "missing_responsible_blocking",
        ),
        (
            {"actions": [_item("Ordinary review", responsible=None)]},
            ReadinessSeverity.WARNING,
            "missing_responsible_warning",
        ),
        (
            {"actions": [_item("Urgent review", priority=WorkItemPriority.HIGH, overdue=True)]},
            ReadinessSeverity.BLOCKING,
            "overdue_blocking",
        ),
        (
            {"actions": [_item("Routine review", overdue=True)]},
            ReadinessSeverity.WARNING,
            "overdue_warning",
        ),
        (
            {
                "manufacturer_inputs": [
                    _item("Data sheet", kind=ExecutionListKind.MANUFACTURER_INPUT)
                ]
            },
            ReadinessSeverity.BLOCKING,
            "manufacturer_not_started",
        ),
        (
            {
                "manufacturer_inputs": [
                    _item(
                        "Test report",
                        kind=ExecutionListKind.MANUFACTURER_INPUT,
                        status=PrepStatus.IN_PROGRESS,
                    )
                ]
            },
            ReadinessSeverity.BLOCKING,
            "manufacturer_awaiting",
        ),
        (
            {
                "manufacturer_inputs": [
                    _item(
                        "Compliance statement",
                        kind=ExecutionListKind.MANUFACTURER_INPUT,
                        status=PrepStatus.BLOCKED,
                    )
                ]
            },
            ReadinessSeverity.BLOCKING,
            "manufacturer_blocked",
        ),
        (
            {
                "commercial": [
                    _item(
                        "Payment terms",
                        kind=ExecutionListKind.COMMERCIAL,
                        status=PrepStatus.BLOCKED,
                        status_label="Clarification",
                        has_customer_term=True,
                    )
                ]
            },
            ReadinessSeverity.BLOCKING,
            "customer_term_clarification",
        ),
        (
            {
                "commercial": [
                    _item(
                        "Delivery assumption",
                        kind=ExecutionListKind.COMMERCIAL,
                        status=PrepStatus.BLOCKED,
                        status_label="Clarification",
                    )
                ]
            },
            ReadinessSeverity.WARNING,
            "clarification_without_customer_term",
        ),
        (
            {
                "commercial": [
                    _item(
                        "Warranty",
                        kind=ExecutionListKind.COMMERCIAL,
                        status_label="Decision needed",
                    )
                ]
            },
            ReadinessSeverity.WARNING,
            "commercial_decision_needed",
        ),
        (
            {
                "actions": [
                    _item(
                        "Resolve exception",
                        status=PrepStatus.BLOCKED,
                        priority=WorkItemPriority.HIGH,
                    )
                ]
            },
            ReadinessSeverity.BLOCKING,
            "critical_action_blocked",
        ),
        (
            {"actions": [_item("Resolve note", status=PrepStatus.BLOCKED)]},
            ReadinessSeverity.WARNING,
            "action_blocked",
        ),
    ],
)
def test_each_readiness_rule_row_in_isolation(
    facts: dict[str, object], severity: ReadinessSeverity, code: str
) -> None:
    report = _report(**facts)
    finding = next(item for item in report.findings if item.code == code)
    assert finding.severity is severity
    assert finding.count == 1
    assert finding.resolve_url.startswith("/bids/BID-READINESS/")


def test_optional_inactive_post_award_and_non_required_inputs_are_information_only() -> None:
    report = _report(
        manufacturer_inputs=[
            _item(
                "Optional certificate",
                kind=ExecutionListKind.MANUFACTURER_INPUT,
                status=PrepStatus.NOT_REQUIRED,
                responsible=None,
                required=False,
            )
        ],
        post_award_deliverable_count=300,
        non_blocking_returnable_count=2,
    )
    assert report.verdict is ExecutionReadinessVerdict.READY_FOR_REVIEW
    assert report.blocking_count == report.warning_count == 0
    assert report.info_count == 303


def test_waiting_finding_names_bid_item_and_incomplete_prerequisite() -> None:
    prerequisite = ExecutionItemLink(
        title="Post-award certified drawing",
        status_label="Not started",
        item_url="/items/post-award-drawing",
    )
    returnable = _item(
        "Mandatory submission schedule",
        kind=ExecutionListKind.RETURNABLE,
        mandatory=True,
        waiting=True,
        prerequisites=[prerequisite],
    )
    report = _report(returnables=[returnable])
    finding = next(item for item in report.findings if item.headline == "Waiting on a prerequisite")
    assert finding.severity is ReadinessSeverity.BLOCKING
    assert "Mandatory submission schedule" in finding.detail
    assert "Post-award certified drawing" in finding.detail


def test_completion_progresses_through_all_three_verdicts() -> None:
    mandatory = _item("Bid form", kind=ExecutionListKind.RETURNABLE, mandatory=True)
    decision = _item(
        "Warranty",
        kind=ExecutionListKind.COMMERCIAL,
        status_label="Decision needed",
    )
    assert _report(returnables=[mandatory], commercial=[decision]).verdict is (
        ExecutionReadinessVerdict.NOT_READY
    )
    complete = mandatory.model_copy(
        update={"status": PrepStatus.COMPLETE, "status_label": "Complete"}
    )
    assert _report(returnables=[complete], commercial=[decision]).verdict is (
        ExecutionReadinessVerdict.READY_WITH_WARNINGS
    )
    assert _report(returnables=[complete]).verdict is ExecutionReadinessVerdict.READY_FOR_REVIEW


def test_assigning_an_owner_removes_only_the_missing_owner_finding() -> None:
    unassigned = _item("Draft cover letter", responsible=None)
    before = _report(actions=[unassigned])
    after = _report(
        actions=[unassigned.model_copy(update={"responsible": "Writer", "unassigned": False})]
    )
    assert any(item.code == "missing_responsible_warning" for item in before.findings)
    assert all(item.code != "missing_responsible_warning" for item in after.findings)


def test_order_wording_and_advisory_are_deterministic_and_non_authorizing() -> None:
    report = _report(
        actions=[_item("WI-PRIVATE", responsible=None, overdue=True)],
        post_award_deliverable_count=1,
    )
    severities = [item.severity for item in report.findings]
    assert severities == sorted(
        severities,
        key={
            ReadinessSeverity.BLOCKING: 0,
            ReadinessSeverity.WARNING: 1,
            ReadinessSeverity.INFO: 2,
        }.get,
    )
    for severity in ReadinessSeverity:
        headlines = [item.headline for item in report.findings if item.severity is severity]
        assert headlines == sorted(headlines, key=str.casefold)
    wording = " ".join(
        f"{item.headline} {item.detail} {item.resolve_label}" for item in report.findings
    )
    assert not any(value in wording for value in ("WI-", "DEL-", "IN_PROGRESS", "NOT_STARTED"))
    assert report.advisory == ADVISORY
    assert "approve" not in type(report).model_fields
    assert "submit" not in type(report).model_fields
    assert "authorize" not in type(report).model_fields


@dataclass(frozen=True)
class ServiceContext:
    db: Database
    bid: Bid
    bids: BidRepository
    work: WorkItemService
    deliverables: DeliverableRepository
    vendors: VendorDocumentService
    execution: BidExecutionService
    ops08: Ops08Repository
    dashboard: ExecutionDashboardService
    readiness: BidExecutionReadinessService


@pytest.fixture
def service_context(tmp_db: Database, bid_repo: BidRepository, valid_bid: Bid) -> ServiceContext:
    bid_repo.create_bid(valid_bid)
    work_repository = WorkItemRepository(tmp_db)
    deliverables = DeliverableRepository(tmp_db)
    vendor_repository = VendorDocumentRepository(tmp_db)
    execution_repository = BidExecutionRepository(tmp_db)
    execution = BidExecutionService(
        execution_repository,
        bid_repo,
        DocumentRepository(tmp_db),
        ExecutionSubjectResolver(work_repository, deliverables, vendor_repository),
        now_factory=lambda: NOW,
    )
    ids = iter(UUID(int=value) for value in range(1100, 1500))
    work = WorkItemService(
        work_repository,
        bid_repo,
        now_factory=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    vendors = VendorDocumentService(
        vendor_repository,
        bid_repo,
        now_factory=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    ops08 = Ops08Repository(tmp_db)
    projection = ExecutionProjectionService(
        work_repository,
        deliverables,
        vendor_repository,
        execution_repository,
        ops08,
        bid_repo,
        BidExecutionTrace(tmp_db),
    )
    dashboard = ExecutionDashboardService(projection, bid_repo, deliverables)
    readiness = BidExecutionReadinessService(dashboard, bid_repo, tmp_db)
    return ServiceContext(
        tmp_db,
        valid_bid,
        bid_repo,
        work,
        deliverables,
        vendors,
        execution,
        ops08,
        dashboard,
        readiness,
    )


def _returnable(
    context: ServiceContext,
    deliverable_id: str,
    title: str,
    **values: object,
) -> str:
    payload: dict[str, object] = {
        "deliverable_id": deliverable_id,
        "bid_id": context.bid.bid_id,
        "title": title,
        "description": f"Prepare {title}",
        "category": "Submission",
        "criticality": DeliverableCriticality.MANDATORY,
        "lifecycle_phase": LifecyclePhase.WITH_BID,
        "direction": DeliverableDirection.INTERNAL,
        "workflow_state": DeliverableState.ACTIVE,
        "provenance": Provenance.from_human("jason"),
        "created_at": NOW,
        "updated_at": NOW,
        "created_by": "jason",
    }
    payload.update(values)
    context.deliverables.create(Deliverable.model_validate(payload), "jason")
    return deliverable_id


def test_service_facts_reconcile_to_dashboard_lists_and_do_not_infer_values(
    service_context: ServiceContext,
) -> None:
    _returnable(service_context, "DEL-BID", "Bid form")
    _returnable(
        service_context,
        "DEL-OPTIONAL",
        "Optional brochure",
        criticality=DeliverableCriticality.INFORMATIONAL,
    )
    _returnable(
        service_context,
        "DEL-POST",
        "Post-award drawing",
        lifecycle_phase=LifecyclePhase.POST_AWARD,
    )
    service_context.work.create_work_item(
        {"bid_id": service_context.bid.bid_id, "title": "Draft response"}, "jason"
    )
    package = service_context.vendors.create_package(
        SupplierPackageCreate(
            bid_id=service_context.bid.bid_id,
            package_name="Pump package",
            package_code="PKG-PUMP",
            proposed_manufacturer="Atlas",
            internal_owner="Vendor lead",
        ),
        "jason",
    )
    service_context.vendors.create_requirement(
        CustomerRequirementCreate(
            package_id=package.package_id,
            customer_requirement_code="DATA",
            deliverable_title="Manufacturer data",
        ),
        "jason",
    )
    facts = service_context.readiness.facts(service_context.bid.bid_id, AS_OF)
    assert len(facts.returnables) == len(
        service_context.dashboard.filtered_list(
            service_context.bid.bid_id, "returnables", ExecutionFilter.ALL, AS_OF
        )
    )
    assert len([item for item in facts.manufacturer_inputs if item.required]) == len(
        service_context.dashboard.filtered_list(
            service_context.bid.bid_id,
            "manufacturer-inputs",
            ExecutionFilter.OUTSTANDING,
            AS_OF,
        )
    )
    assert len(facts.actions) == len(
        service_context.dashboard.filtered_list(
            service_context.bid.bid_id, "actions", ExecutionFilter.ALL, AS_OF
        )
    )
    bid_form = next(item for item in facts.returnables if item.title == "Bid form")
    action = next(item for item in facts.actions if item.title == "Draft response")
    assert bid_form.responsible is None and bid_form.internal_due_date is None
    assert action.responsible is None and action.internal_due_date is None
    assert facts.post_award_deliverable_count == 1
    assert facts.non_blocking_returnable_count == 1


def test_resolved_clarification_is_info_but_not_in_dashboard_clarification_count(
    service_context: ServiceContext,
) -> None:
    position_id = service_context.ops08.ensure_position(
        service_context.bid.bid_id, "PAYMENT_TERMS", "Commercial lead", "jason"
    )
    current = service_context.ops08.detail(position_id)
    service_context.ops08.revise(
        position_id,
        int(current["current_version"]),
        PositionRevision(
            disposition=PositionDisposition.CLARIFICATION_REQUIRED,
            owner="Commercial lead",
            due_date=AS_OF,
            current_outcome="Customer confirmed milestones",
            negotiation_state=NegotiationState.RESOLVED,
        ),
        "jason",
    )
    report = service_context.readiness.evaluate(service_context.bid.bid_id, AS_OF)
    clarification_rows = service_context.dashboard.filtered_list(
        service_context.bid.bid_id, "commercial", ExecutionFilter.CLARIFICATION, AS_OF
    )
    assert clarification_rows == []
    assert any(item.code == "clarifications_resolved" for item in report.findings)
    assert all(
        item.code not in {"customer_term_clarification", "clarification_without_customer_term"}
        for item in report.findings
    )


def test_existing_approval_blocker_uses_control_center_destination(
    service_context: ServiceContext,
) -> None:
    service_context.bids.update_bid(
        service_context.bid.model_copy(update={"classification": BidLevel.LEVEL_2})
    )
    report = service_context.readiness.evaluate(service_context.bid.bid_id, AS_OF)
    margin = next(item for item in report.findings if item.code == "approval:g4.margin_approved")
    assert margin.severity is ReadinessSeverity.BLOCKING
    assert margin.headline == "Margin approval required"
    assert margin.resolve_url.endswith("&approval_type=MARGIN#gate-approval")


def test_dashboard_tile_carries_optional_readiness_result(service_context: ServiceContext) -> None:
    _returnable(service_context, "DEL-BLOCK", "Mandatory form")
    dashboard_with_readiness = ExecutionDashboardService(
        service_context.dashboard.projection,
        service_context.bids,
        service_context.deliverables,
        service_context.readiness,
    )
    tile = next(
        item
        for item in dashboard_with_readiness.dashboard(service_context.bid.bid_id, AS_OF).tiles
        if item.key == "readiness"
    )
    assert tile.verdict == "Not ready"
    assert tile.blocking_count and tile.count == tile.blocking_count


def test_post_award_prerequisite_blocks_only_the_bid_stage_item(
    service_context: ServiceContext,
) -> None:
    bid_item = _returnable(service_context, "DEL-BID-STAGE", "Mandatory response")
    post_award = _returnable(
        service_context,
        "DEL-POST-STAGE",
        "Post-award drawing",
        lifecycle_phase=LifecyclePhase.POST_AWARD,
    )
    service_context.execution.add_dependency(
        service_context.bid.bid_id,
        ExecutionSubjectKind.RETURNABLE,
        bid_item,
        ExecutionSubjectKind.RETURNABLE,
        post_award,
        None,
        "jason",
    )
    report = service_context.readiness.evaluate(service_context.bid.bid_id, AS_OF)
    waiting = next(item for item in report.findings if item.headline == "Waiting on a prerequisite")
    assert waiting.severity is ReadinessSeverity.BLOCKING
    assert "Mandatory response" in waiting.detail and "Post-award drawing" in waiting.detail
    assert not any(
        title == "Post-award drawing"
        for finding in report.findings
        if finding.severity is not ReadinessSeverity.INFO
        for title, _url_value in finding.items
        if finding.headline != "Waiting on a prerequisite"
    )
