"""Read-only FastAPI routes for the Bid execution workspace."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any, Protocol
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from core.bid_execution import ExecutionSubjectKind
from core.bid_execution_dashboard import (
    BidExecutionDashboardNotFoundError,
    ExecutionDashboardService,
    ExecutionListSlug,
)
from core.bid_execution_projection import (
    ExecutionFilter,
    ExecutionItem,
    ExecutionListKind,
    ExecutionProjectionService,
)
from core.bid_execution_readiness import BidExecutionReadinessService
from core.bid_execution_readiness_models import ReadinessSeverity
from core.bid_execution_service import BidExecutionService
from core.bid_execution_trace import BidExecutionTrace
from core.bid_repository import BidRepository
from core.deliverable_discovery_repository import DeliverableDiscoveryRepository
from core.document_repository import DocumentRepository
from core.requirement_lifecycle import DiscoveryLifecycle
from core.vendor_document_repository import VendorDocumentRepository


class Render(Protocol):
    """The application's small Jinja rendering boundary."""

    def __call__(
        self, template_name: str, status_code: int = 200, **context: object
    ) -> HTMLResponse: ...


class ReceivedFileRepository(Protocol):
    """Read boundary needed to name a finding's received source file."""

    def file(self, file_id: str, bid_id: str) -> dict[str, object]: ...


_TABS: dict[ExecutionListSlug, tuple[tuple[ExecutionFilter, str], ...]] = {
    ExecutionListSlug.RETURNABLES: (
        (ExecutionFilter.ALL, "All"),
        (ExecutionFilter.OUTSTANDING, "Outstanding"),
        (ExecutionFilter.IN_PROGRESS, "In progress"),
        (ExecutionFilter.BLOCKED, "Blocked"),
        (ExecutionFilter.COMPLETE, "Complete"),
        (ExecutionFilter.UNASSIGNED, "Unassigned"),
        (ExecutionFilter.OVERDUE, "Overdue"),
    ),
    ExecutionListSlug.MANUFACTURER_INPUTS: (
        (ExecutionFilter.ALL, "All"),
        (ExecutionFilter.OUTSTANDING, "Outstanding"),
        (ExecutionFilter.IN_PROGRESS, "In progress"),
        (ExecutionFilter.BLOCKED, "Blocked"),
        (ExecutionFilter.COMPLETE, "Complete"),
        (ExecutionFilter.UNASSIGNED, "Unassigned"),
        (ExecutionFilter.OVERDUE, "Overdue"),
    ),
    ExecutionListSlug.ACTIONS: (
        (ExecutionFilter.ALL, "All"),
        (ExecutionFilter.OUTSTANDING, "Outstanding"),
        (ExecutionFilter.IN_PROGRESS, "In progress"),
        (ExecutionFilter.BLOCKED, "Blocked"),
        (ExecutionFilter.COMPLETE, "Complete"),
        (ExecutionFilter.UNASSIGNED, "Unassigned"),
        (ExecutionFilter.OVERDUE, "Overdue"),
    ),
    ExecutionListSlug.COMMERCIAL: (
        (ExecutionFilter.ALL, "All"),
        (ExecutionFilter.CLARIFICATION, "Clarification"),
        (ExecutionFilter.DECISION, "Decision"),
    ),
    ExecutionListSlug.OVERDUE: (
        (ExecutionFilter.OVERDUE, "Overdue"),
        (ExecutionFilter.BLOCKED, "Blocked"),
        (ExecutionFilter.ALL, "All"),
    ),
}

_DEFAULT_FILTER = {
    ExecutionListSlug.RETURNABLES: ExecutionFilter.OUTSTANDING,
    ExecutionListSlug.MANUFACTURER_INPUTS: ExecutionFilter.OUTSTANDING,
    ExecutionListSlug.ACTIONS: ExecutionFilter.OUTSTANDING,
    ExecutionListSlug.COMMERCIAL: ExecutionFilter.CLARIFICATION,
    ExecutionListSlug.OVERDUE: ExecutionFilter.OVERDUE,
}

_LIST_LABELS = {
    ExecutionListSlug.RETURNABLES: "Bid returnables",
    ExecutionListSlug.MANUFACTURER_INPUTS: "Manufacturer inputs",
    ExecutionListSlug.ACTIONS: "Pre-bid actions",
    ExecutionListSlug.COMMERCIAL: "Commercial issues",
    ExecutionListSlug.OVERDUE: "Items needing attention",
}

_ITEM_KINDS = {
    "work-item": (ExecutionSubjectKind.WORK_ITEM, ExecutionListKind.ACTION),
    "returnable": (ExecutionSubjectKind.RETURNABLE, ExecutionListKind.RETURNABLE),
    "manufacturer-input": (
        ExecutionSubjectKind.MANUFACTURER_INPUT,
        ExecutionListKind.MANUFACTURER_INPUT,
    ),
}

_LIFECYCLE_LABELS = {
    DiscoveryLifecycle.BID_RETURNABLE.value: "With the bid",
    DiscoveryLifecycle.PRE_BID_ACTION.value: "Before bidding",
    DiscoveryLifecycle.MANUFACTURER_INPUT.value: "From the manufacturer",
    DiscoveryLifecycle.POST_AWARD.value: "After award",
    DiscoveryLifecycle.CONDITIONAL.value: "Conditional or optional",
    DiscoveryLifecycle.NOT_APPLICABLE.value: "Not applicable",
    DiscoveryLifecycle.NEEDS_REVIEW.value: "Needs review",
}

_DISCOVERY_MARKER = re.compile(r"\s*\[DISC:[^\]]+]\s*")


def _error(render: Render, message: str, status_code: int) -> HTMLResponse:
    return render("bid_execution_error.html", status_code=status_code, message=message)


def _deadline_wording(days: int) -> str:
    if days == 0:
        return "closes today"
    if days > 0:
        return f"{days} day{'s' if days != 1 else ''} left"
    past = abs(days)
    return f"{past} day{'s' if past != 1 else ''} past"


def _find_item(
    projection: ExecutionProjectionService,
    bid_id: str,
    list_kind: ExecutionListKind,
    subject_id: str,
    as_of: date,
) -> ExecutionItem | None:
    rows = projection.items(bid_id, list_kind, ExecutionFilter.ALL, as_of)
    if list_kind is ExecutionListKind.MANUFACTURER_INPUT:
        rows += projection.items(bid_id, list_kind, ExecutionFilter.NOT_REQUIRED, as_of)
    item = next((row for row in rows if row.subject_id == subject_id), None)
    return _display_item(item) if item is not None else None


def _display_item(item: ExecutionItem) -> ExecutionItem:
    """Remove storage-only discovery markers from business-facing descriptions."""
    description = _DISCOVERY_MARKER.sub(" ", item.description).strip()
    return item.model_copy(update={"description": description})


def _document_name(
    source: dict[str, Any],
    bid_id: str,
    package_repository: ReceivedFileRepository,
    document_repository: DocumentRepository,
) -> str:
    received_file_id = source.get("received_file_id")
    if received_file_id:
        try:
            row = package_repository.file(str(received_file_id), bid_id)
            return str(row.get("original_filename") or row.get("original_relative_path"))
        except ValueError:
            return "Source document"
    version_id = source.get("document_version_id")
    version = document_repository.get_version(str(version_id)) if version_id else None
    if version is None:
        return "Source document"
    document = document_repository.get(version.document_id)
    return document.title if document is not None else version.original_filename


def register_bid_execution_routes(
    app: FastAPI,
    *,
    render: Render,
    working_date: Callable[[], date],
    bid_repository: BidRepository,
    execution_service: BidExecutionService,
    projection_service: ExecutionProjectionService,
    dashboard_service: ExecutionDashboardService,
    readiness_service: BidExecutionReadinessService,
    trace: BidExecutionTrace,
    discovery_repository: DeliverableDiscoveryRepository,
    bid_package_repository: ReceivedFileRepository,
    document_repository: DocumentRepository,
    vendor_document_repository: VendorDocumentRepository,
) -> None:
    """Register the read-only S3-5a execution screens on the application."""

    def bid_or_error(bid_id: str) -> object | None:
        return bid_repository.get_bid(bid_id)

    @app.get("/bids/{bid_id}/execution", response_class=HTMLResponse)
    async def bid_execution_dashboard(bid_id: str) -> HTMLResponse:
        bid = bid_or_error(bid_id)
        if bid is None:
            return _error(render, "Bid not found. Return to the Bid list and choose a Bid.", 404)
        as_of = working_date()
        try:
            dashboard = dashboard_service.dashboard(bid_id, as_of)
            next_rows = dashboard_service.filtered_list(
                bid_id,
                ExecutionListSlug.RETURNABLES,
                ExecutionFilter.UNASSIGNED,
                as_of,
            )
        except (BidExecutionDashboardNotFoundError, ValueError) as exc:
            return _error(render, str(exc), 404)
        next_item = next((_display_item(item) for item in next_rows if item.mandatory), None)
        return render(
            "bid_execution_dashboard.html",
            bid=bid,
            dashboard=dashboard,
            deadline_wording=_deadline_wording(dashboard.days_to_deadline),
            next_item=next_item,
        )

    @app.get("/bids/{bid_id}/execution/readiness", response_class=HTMLResponse)
    async def bid_execution_readiness(bid_id: str) -> HTMLResponse:
        bid = bid_or_error(bid_id)
        if bid is None:
            return _error(render, "Bid not found. Return to the Bid list and choose a Bid.", 404)
        try:
            readiness = readiness_service.evaluate(bid_id, working_date())
        except ValueError as exc:
            return _error(render, str(exc), 422)
        groups = (
            (
                "Blocking",
                [
                    item
                    for item in readiness.findings
                    if item.severity is ReadinessSeverity.BLOCKING
                ],
            ),
            (
                "Warnings",
                [item for item in readiness.findings if item.severity is ReadinessSeverity.WARNING],
            ),
            (
                "Information",
                [item for item in readiness.findings if item.severity is ReadinessSeverity.INFO],
            ),
        )
        return render("bid_execution_readiness.html", bid=bid, readiness=readiness, groups=groups)

    @app.get("/bids/{bid_id}/execution/source/{finding_id}", response_class=HTMLResponse)
    async def bid_execution_source(bid_id: str, finding_id: str) -> HTMLResponse:
        bid = bid_or_error(bid_id)
        if bid is None:
            return _error(render, "Bid not found. Return to the Bid list and choose a Bid.", 404)
        proposal = discovery_repository.get_proposal(finding_id)
        if proposal is None or str(proposal["bid_id"]) != bid_id:
            return _error(render, "Finding not found in this Bid.", 404)
        decision = discovery_repository.current_decision(finding_id)
        decision_labels = {
            "PENDING": "Pending review",
            "ACCEPTED": "Accepted",
            "REJECTED": "Rejected",
            "DEFERRED": "Deferred",
        }
        sources = [
            {
                "document": _document_name(
                    source,
                    bid_id,
                    bid_package_repository,
                    document_repository,
                ),
                "locator": source.get("locator"),
                "quote": source.get("quote"),
            }
            for source in proposal.get("sources", [])
        ]
        return render(
            "bid_execution_source.html",
            bid=bid,
            finding=proposal,
            sources=sources,
            classification_label=_LIFECYCLE_LABELS.get(
                str(proposal.get("lifecycle")), "Needs review"
            ),
            review_decision=decision_labels.get(
                str(decision["decision"]) if decision else "PENDING", "Pending review"
            ),
        )

    @app.get(
        "/bids/{bid_id}/execution/items/{kind_slug}/{subject_id}",
        response_class=HTMLResponse,
    )
    async def bid_execution_item(bid_id: str, kind_slug: str, subject_id: str) -> HTMLResponse:
        bid = bid_or_error(bid_id)
        if bid is None:
            return _error(render, "Bid not found. Return to the Bid list and choose a Bid.", 404)
        kind_pair = _ITEM_KINDS.get(kind_slug)
        if kind_pair is None:
            return _error(render, "Execution item type not found.", 404)
        subject_kind, list_kind = kind_pair
        try:
            notes = execution_service.list_notes(bid_id, subject_kind, subject_id)
            record = execution_service.get_record(bid_id, subject_kind, subject_id)
            source_trace = trace.source_trace(bid_id, subject_kind, subject_id)
            item = _find_item(projection_service, bid_id, list_kind, subject_id, working_date())
        except ValueError as exc:
            return _error(render, str(exc), 404)
        if item is None:
            return _error(render, "Execution item not found in this Bid.", 404)
        evidence_label = item.evidence
        if evidence_label is None and record is not None and record.evidence_document_version_id:
            version = document_repository.get_version(record.evidence_document_version_id)
            if version is not None:
                document = document_repository.get(version.document_id)
                evidence_label = (
                    document.title if document is not None else version.original_filename
                )
        manufacturer_url = None
        if subject_kind is ExecutionSubjectKind.MANUFACTURER_INPUT:
            requirement = vendor_document_repository.get_requirement(subject_id)
            if requirement is not None:
                manufacturer_url = (
                    f"/vendor-documents/packages/{quote(requirement.package_id)}"
                    f"#requirement-{quote(subject_id)}"
                )
        list_slug = {
            ExecutionListKind.ACTION: ExecutionListSlug.ACTIONS,
            ExecutionListKind.RETURNABLE: ExecutionListSlug.RETURNABLES,
            ExecutionListKind.MANUFACTURER_INPUT: ExecutionListSlug.MANUFACTURER_INPUTS,
        }[list_kind]
        return render(
            "bid_execution_item.html",
            bid=bid,
            item=item,
            record=record,
            notes=list(reversed(notes)),
            source_trace=source_trace,
            evidence_label=evidence_label,
            list_url=(f"/bids/{quote(bid_id)}/execution/{list_slug.value}?state=all"),
            my_work_url=(
                f"/my-work/{quote(subject_id)}"
                if subject_kind is ExecutionSubjectKind.WORK_ITEM
                else None
            ),
            manufacturer_url=manufacturer_url,
        )

    @app.get("/bids/{bid_id}/execution/{list_slug}", response_class=HTMLResponse)
    async def bid_execution_list(
        bid_id: str, list_slug: str, state: str | None = None
    ) -> HTMLResponse:
        bid = bid_or_error(bid_id)
        if bid is None:
            return _error(render, "Bid not found. Return to the Bid list and choose a Bid.", 404)
        try:
            slug = ExecutionListSlug(list_slug)
        except ValueError:
            return _error(render, "Execution list not found.", 404)
        tabs = _TABS[slug]
        allowed = {tab_state for tab_state, _label in tabs}
        if state is None:
            selected = _DEFAULT_FILTER[slug]
        else:
            try:
                selected = ExecutionFilter(state)
            except ValueError:
                return _error(render, "Unknown list filter. Choose one of the listed tabs.", 422)
            if selected not in allowed:
                return _error(render, "That filter is not available for this list.", 422)
        as_of = working_date()
        try:
            rows = [
                _display_item(item)
                for item in dashboard_service.filtered_list(bid_id, slug, selected, as_of)
            ]
            tab_views = [
                {
                    "state": tab_state.value,
                    "label": label,
                    "count": len(dashboard_service.filtered_list(bid_id, slug, tab_state, as_of)),
                }
                for tab_state, label in tabs
            ]
        except ValueError as exc:
            return _error(render, str(exc), 422)
        state_label = next(label for tab_state, label in tabs if tab_state is selected)
        return render(
            "bid_execution_list.html",
            bid=bid,
            rows=rows,
            tabs=tab_views,
            selected_state=selected.value,
            state_label=state_label,
            list_label=_LIST_LABELS[slug],
            list_slug=slug.value,
            title=(
                _LIST_LABELS[slug]
                if selected is ExecutionFilter.ALL
                else f"{state_label} {_LIST_LABELS[slug].lower()}"
            ),
            empty_message=f"No {_LIST_LABELS[slug].lower()} match the {state_label.lower()} view.",
            show_requirement=slug is ExecutionListSlug.RETURNABLES,
        )


__all__ = ["register_bid_execution_routes"]
