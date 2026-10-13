"""FastAPI routes for the Bid execution workspace."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any, Protocol, TypedDict
from urllib.parse import quote, urlencode, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from core.bid_execution import ExecutionSubjectKind
from core.bid_execution_actions import BidExecutionActionService
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
from core.work_items import WorkItemPriority


class Render(Protocol):
    """The application's small Jinja rendering boundary."""

    def __call__(
        self, template_name: str, status_code: int = 200, **context: object
    ) -> HTMLResponse: ...


class ReceivedFileRepository(Protocol):
    """Read boundary needed to name a finding's received source file."""

    def file(self, file_id: str, bid_id: str) -> dict[str, object]: ...


class Feedback(TypedDict):
    """Bounded query-string feedback rendered by execution pages."""

    notice: str | None
    form_error: str | None
    next_item_url: str | None
    next_kind_label: str


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
_NOTICE_LIMIT = 500
_VERSION_CONFLICT_MARKERS = (
    "stale work item version",
    "updated by someone else",
    "execution record changed",
    "changed since you opened",
    "changed while",
)


def _error(render: Render, message: str, status_code: int) -> HTMLResponse:
    return render("bid_execution_error.html", status_code=status_code, message=message)


def _safe_execution_return_to(bid_id: str, value: str | None) -> str | None:
    """Accept only a local return path inside the current Bid execution workspace."""
    if (
        not value
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        return None
    parsed = urlsplit(value)
    root = f"/bids/{quote(bid_id, safe='')}/execution"
    if (
        parsed.scheme
        or parsed.netloc
        or not (parsed.path == root or parsed.path.startswith(f"{root}/"))
    ):
        return None
    return value


def _feedback(request: Request, bid_id: str) -> Feedback:
    notice = request.query_params.get("notice")
    error = request.query_params.get("error")
    return {
        "notice": notice[:_NOTICE_LIMIT] if notice else None,
        "form_error": error[:_NOTICE_LIMIT] if error else None,
        "next_item_url": _safe_execution_return_to(
            bid_id, request.query_params.get("next_item_url")
        ),
        "next_kind_label": (request.query_params.get("next_kind") or "item")[:40],
    }


def _exception_message(exc: ValueError | ValidationError) -> str:
    """Turn validation and concurrency failures into one actionable screen message."""
    raw = str(exc)
    lowered = raw.casefold()
    if any(marker in lowered for marker in _VERSION_CONFLICT_MARKERS):
        return "This item changed since you opened it. Reload and try again."
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_url=False)
        detail = str(errors[0].get("msg", "The entered value is invalid")) if errors else raw
        return f"{detail}. Correct the highlighted form values and try again."
    return raw[:_NOTICE_LIMIT]


def _optional_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("the date is invalid; enter a calendar date in YYYY-MM-DD format") from exc


def _contributors(value: object) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _priority(value: object) -> WorkItemPriority | None:
    text = str(value or "").strip().upper().replace(" ", "_")
    if not text:
        return None
    try:
        return WorkItemPriority(text)
    except ValueError as exc:
        raise ValueError("priority is invalid; choose one of the listed priorities") from exc


def _encoded_path_id(value: str) -> str:
    """Keep an identifier in a URL without exposing its familiar prefix in page text."""
    return quote(value, safe="").replace("-", "%2D")


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
    action_service: BidExecutionActionService,
    actor: str,
) -> None:
    """Register the S3-5 Bid execution screens and form commands."""

    def bid_or_error(bid_id: str) -> object | None:
        return bid_repository.get_bid(bid_id)

    def all_items(bid_id: str) -> list[tuple[ExecutionSubjectKind, ExecutionItem]]:
        values: list[tuple[ExecutionSubjectKind, ExecutionItem]] = []
        for subject_kind, list_kind in (
            (ExecutionSubjectKind.WORK_ITEM, ExecutionListKind.ACTION),
            (ExecutionSubjectKind.RETURNABLE, ExecutionListKind.RETURNABLE),
            (ExecutionSubjectKind.MANUFACTURER_INPUT, ExecutionListKind.MANUFACTURER_INPUT),
        ):
            rows = projection_service.items(bid_id, list_kind, ExecutionFilter.ALL, working_date())
            if subject_kind is ExecutionSubjectKind.MANUFACTURER_INPUT:
                rows += projection_service.items(
                    bid_id, list_kind, ExecutionFilter.NOT_REQUIRED, working_date()
                )
            seen: set[str] = set()
            for row in rows:
                if row.subject_id not in seen:
                    values.append((subject_kind, _display_item(row)))
                    seen.add(row.subject_id)
        return values

    def candidate_options(
        bid_id: str,
        *,
        exclude: tuple[ExecutionSubjectKind, str] | None = None,
    ) -> list[dict[str, object]]:
        options: list[dict[str, object]] = []
        for index, (kind, item) in enumerate(all_items(bid_id)):
            if exclude == (kind, item.subject_id):
                continue
            options.append(
                {
                    "token": f"candidate-{index}",
                    "kind": kind,
                    "kind_label": kind.label,
                    "subject_id": item.subject_id,
                    "title": item.title,
                    "status_label": item.status_label,
                    "item_url": item.item_url,
                }
            )
        return options

    def candidate_option_groups(
        options: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        groups: list[dict[str, object]] = []
        for kind, label in (
            (ExecutionSubjectKind.RETURNABLE, "Returnables"),
            (ExecutionSubjectKind.MANUFACTURER_INPUT, "Manufacturer inputs"),
            (ExecutionSubjectKind.WORK_ITEM, "Actions"),
        ):
            groups.append(
                {
                    "label": label,
                    "options": [option for option in options if option["kind"] is kind],
                }
            )
        # Commercial positions are read-only and cannot be dependency subjects in Stage 3.
        groups.append({"label": "Commercial", "options": []})
        return groups

    def resolve_candidate(bid_id: str, token: str) -> tuple[ExecutionSubjectKind, str] | None:
        normalized = token.strip()
        if not normalized:
            return None
        options = candidate_options(bid_id)
        selected = next((row for row in options if row["token"] == normalized), None)
        if selected is not None:
            return selected["kind"], str(selected["subject_id"])  # type: ignore[return-value]
        if "|" in normalized:
            raw_kind, subject_id = normalized.split("|", 1)
            try:
                kind = action_service._subject_kind(raw_kind)
                action_service.execution.subject_resolver.resolve(bid_id, kind, subject_id)
            except ValueError as exc:
                raise ValueError(
                    "the selected prerequisite is no longer available; reload and choose a "
                    "listed item"
                ) from exc
            return kind, subject_id
        raise ValueError(
            "the selected prerequisite is no longer available; reload and choose a listed item"
        )

    def controlled_document_options(bid_id: str) -> list[dict[str, str]]:
        options: list[dict[str, str]] = []
        for index, document in enumerate(document_repository.list_documents(bid_id=bid_id)):
            version = document_repository.get_version(document.current_version_id)
            options.append(
                {
                    "token": f"document-{index}",
                    "version_id": document.current_version_id,
                    "label": (
                        f"{document.title} — {version.version_label}"
                        if version is not None
                        else document.title
                    ),
                }
            )
        return options

    def resolve_document(bid_id: str, token: str) -> str | None:
        normalized = token.strip()
        if not normalized:
            return None
        option = next(
            (row for row in controlled_document_options(bid_id) if row["token"] == normalized),
            None,
        )
        if option is not None:
            return option["version_id"]
        version = document_repository.get_version(normalized)
        document = document_repository.get(version.document_id) if version is not None else None
        if document is None or document.bid_id != bid_id:
            raise ValueError(
                "the selected controlled document is no longer available; reload and choose "
                "evidence from this Bid"
            )
        return normalized

    def redirect_result(
        bid_id: str,
        return_to: str | None,
        fallback: str,
        result: object,
    ) -> RedirectResponse:
        target = _safe_execution_return_to(bid_id, return_to) or fallback
        message = str(getattr(result, "message", "Saved."))[:_NOTICE_LIMIT]
        next_url = _safe_execution_return_to(bid_id, getattr(result, "next_item_url", None))
        kind = getattr(result, "subject_kind", ExecutionSubjectKind.WORK_ITEM)
        kind_label = {
            ExecutionSubjectKind.WORK_ITEM: "action",
            ExecutionSubjectKind.RETURNABLE: "returnable",
            ExecutionSubjectKind.MANUFACTURER_INPUT: "manufacturer input",
        }.get(kind, "item")
        params = {"notice": message}
        if next_url is not None:
            params.update(next_item_url=next_url, next_kind=kind_label)
        separator = "&" if "?" in target else "?"
        return RedirectResponse(f"{target}{separator}{urlencode(params)}", status_code=303)

    def item_response(
        request: Request,
        bid_id: str,
        kind_slug: str,
        subject_id: str,
        *,
        status_code: int = 200,
        entered: dict[str, object] | None = None,
        active_form: str | None = None,
        form_error: str | None = None,
    ) -> HTMLResponse:
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
        work_item = (
            action_service.work_items.get_work_item(subject_id)
            if subject_kind is ExecutionSubjectKind.WORK_ITEM
            else None
        )
        candidates = candidate_options(bid_id, exclude=(subject_kind, subject_id))
        candidate_by_subject = {
            (row["kind"], row["subject_id"]): row for row in candidate_options(bid_id)
        }
        dependencies = []
        for dependency in execution_service.repository.list_active_dependencies_for_subject(
            bid_id, subject_kind, subject_id
        ):
            if dependency.item_kind is not subject_kind or dependency.item_id != subject_id:
                continue
            linked = candidate_by_subject.get(
                (dependency.prerequisite_kind, dependency.prerequisite_id)
            )
            if linked is not None:
                dependencies.append({"dependency_id": dependency.dependency_id, **linked})
        list_slug = {
            ExecutionListKind.ACTION: ExecutionListSlug.ACTIONS,
            ExecutionListKind.RETURNABLE: ExecutionListSlug.RETURNABLES,
            ExecutionListKind.MANUFACTURER_INPUT: ExecutionListSlug.MANUFACTURER_INPUTS,
        }[list_kind]
        context = _feedback(request, bid_id)
        if form_error is not None:
            context["form_error"] = form_error[:_NOTICE_LIMIT]
        item_form_base = (
            f"/bids/{quote(bid_id)}/execution/items/{kind_slug}/{_encoded_path_id(subject_id)}"
        )
        return render(
            "bid_execution_item.html",
            status_code=status_code,
            bid=bid,
            item=item,
            subject_kind=subject_kind,
            kind_slug=kind_slug,
            record=record,
            work_item=work_item,
            expected_version=(
                work_item.version if work_item is not None else record.version if record else 0
            ),
            overlay_version=record.version if record else 0,
            notes=list(reversed(notes)),
            source_trace=source_trace,
            evidence_label=evidence_label,
            prerequisite_groups=candidate_option_groups(candidates),
            active_prerequisites=dependencies,
            document_options=controlled_document_options(bid_id),
            priorities=list(WorkItemPriority),
            entered=entered or {},
            active_form=active_form,
            list_url=(f"/bids/{quote(bid_id)}/execution/{list_slug.value}?state=all"),
            return_to=item_form_base,
            item_form_base=item_form_base,
            my_work_url=(
                f"/my-work/{quote(subject_id)}"
                if subject_kind is ExecutionSubjectKind.WORK_ITEM
                else None
            ),
            manufacturer_url=manufacturer_url,
            **context,
        )

    def list_response(
        request: Request,
        bid_id: str,
        list_slug: str,
        state: str | None,
        *,
        status_code: int = 200,
        entered: dict[str, object] | None = None,
        active_form: str | None = None,
        form_error: str | None = None,
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
        record_versions: dict[str, int] = {}
        if slug in {
            ExecutionListSlug.RETURNABLES,
            ExecutionListSlug.MANUFACTURER_INPUTS,
        }:
            record_kind = (
                ExecutionSubjectKind.RETURNABLE
                if slug is ExecutionListSlug.RETURNABLES
                else ExecutionSubjectKind.MANUFACTURER_INPUT
            )
            for row in rows:
                current_record = execution_service.get_record(bid_id, record_kind, row.subject_id)
                record_versions[row.subject_id] = (
                    current_record.version if current_record is not None else 0
                )
        all_subjects = (
            {
                row.subject_id: f"row-{index}"
                for index, row in enumerate(
                    dashboard_service.filtered_list(bid_id, slug, ExecutionFilter.ALL, as_of)
                )
            }
            if slug
            in {
                ExecutionListSlug.RETURNABLES,
                ExecutionListSlug.MANUFACTURER_INPUTS,
            }
            else {}
        )
        context = _feedback(request, bid_id)
        if form_error is not None:
            context["form_error"] = form_error[:_NOTICE_LIMIT]
        return render(
            "bid_execution_list.html",
            status_code=status_code,
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
            show_assign=slug
            in {
                ExecutionListSlug.RETURNABLES,
                ExecutionListSlug.MANUFACTURER_INPUTS,
            },
            show_add_action=slug is ExecutionListSlug.ACTIONS,
            show_source_confirmation=slug is ExecutionListSlug.MANUFACTURER_INPUTS,
            record_versions=record_versions,
            row_tokens=all_subjects,
            quick_urls={
                row.subject_id: (
                    f"/bids/{quote(bid_id)}/execution/{slug.value}/items/"
                    f"{_encoded_path_id(row.subject_id)}/quick-assign"
                )
                for row in rows
            },
            confirm_urls={
                row.subject_id: (
                    f"/bids/{quote(bid_id)}/execution/manufacturer-inputs/items/"
                    f"{_encoded_path_id(row.subject_id)}/confirm-source-owner"
                )
                for row in rows
                if slug is ExecutionListSlug.MANUFACTURER_INPUTS
            },
            prerequisite_groups=candidate_option_groups(candidate_options(bid_id)),
            priorities=list(WorkItemPriority),
            entered=entered or {},
            active_form=active_form,
            return_to=str(request.url.path)
            + (f"?state={selected.value}" if state is not None else ""),
            **context,
        )

    @app.get("/bids/{bid_id}/execution", response_class=HTMLResponse)
    async def bid_execution_dashboard(request: Request, bid_id: str) -> HTMLResponse:
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
            **_feedback(request, bid_id),
        )

    @app.get("/bids/{bid_id}/execution/readiness", response_class=HTMLResponse)
    async def bid_execution_readiness(request: Request, bid_id: str) -> HTMLResponse:
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
        return render(
            "bid_execution_readiness.html",
            bid=bid,
            readiness=readiness,
            groups=groups,
            **_feedback(request, bid_id),
        )

    @app.get("/bids/{bid_id}/execution/source/{finding_id}", response_class=HTMLResponse)
    async def bid_execution_source(request: Request, bid_id: str, finding_id: str) -> HTMLResponse:
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
            **_feedback(request, bid_id),
        )

    @app.get(
        "/bids/{bid_id}/execution/items/{kind_slug}/{subject_id}",
        response_class=HTMLResponse,
    )
    async def bid_execution_item(
        request: Request, bid_id: str, kind_slug: str, subject_id: str
    ) -> HTMLResponse:
        return item_response(request, bid_id, kind_slug, subject_id)

    @app.get("/bids/{bid_id}/execution/{list_slug}", response_class=HTMLResponse)
    async def bid_execution_list(
        request: Request, bid_id: str, list_slug: str, state: str | None = None
    ) -> HTMLResponse:
        return list_response(request, bid_id, list_slug, state)

    @app.post("/bids/{bid_id}/execution/items/{kind_slug}/{subject_id}/plan")
    async def save_execution_plan(
        request: Request, bid_id: str, kind_slug: str, subject_id: str
    ) -> Response:
        form = await request.form()
        entered: dict[str, object] = {str(key): value for key, value in form.items()}
        fallback = f"/bids/{quote(bid_id)}/execution/items/{kind_slug}/{quote(subject_id)}"
        try:
            pair = _ITEM_KINDS.get(kind_slug)
            if pair is None:
                raise ValueError(
                    "execution item type was not found; return to the list and choose an item"
                )
            kind = pair[0]
            expected_version = int(str(form.get("expected_version", "0")))
            common: dict[str, object] = {
                "responsible": str(form.get("responsible") or ""),
                "contributors": _contributors(form.get("contributors")),
            }
            if kind is ExecutionSubjectKind.WORK_ITEM:
                result = action_service.update_action(
                    bid_id,
                    subject_id,
                    {
                        **common,
                        "expected_version": expected_version,
                        "overlay_expected_version": int(
                            str(form.get("overlay_expected_version", "0"))
                        ),
                        "due_date": _optional_date(form.get("internal_due_date")),
                        "priority": _priority(form.get("priority")),
                    },
                    actor,
                )
            elif kind is ExecutionSubjectKind.RETURNABLE:
                result = action_service.update_returnable(
                    bid_id,
                    subject_id,
                    {
                        **common,
                        "expected_version": expected_version,
                        "internal_due_date": _optional_date(form.get("internal_due_date")),
                        "priority": _priority(form.get("priority")),
                    },
                    actor,
                )
            else:
                result = action_service.update_manufacturer_input(
                    bid_id,
                    subject_id,
                    {
                        **common,
                        "expected_version": expected_version,
                        "internal_due_date": _optional_date(form.get("internal_due_date")),
                        "priority": _priority(form.get("priority")),
                    },
                    actor,
                )
        except (ValidationError, ValueError) as exc:
            return item_response(
                request,
                bid_id,
                kind_slug,
                subject_id,
                status_code=422,
                entered=entered,
                active_form="plan",
                form_error=_exception_message(exc),
            )
        return redirect_result(bid_id, str(form.get("return_to") or "") or None, fallback, result)

    @app.post("/bids/{bid_id}/execution/items/{kind_slug}/{subject_id}/progress")
    async def save_execution_progress(
        request: Request, bid_id: str, kind_slug: str, subject_id: str
    ) -> Response:
        form = await request.form()
        entered: dict[str, object] = {str(key): value for key, value in form.items()}
        fallback = f"/bids/{quote(bid_id)}/execution/items/{kind_slug}/{quote(subject_id)}"
        try:
            pair = _ITEM_KINDS.get(kind_slug)
            if pair is None:
                raise ValueError(
                    "execution item type was not found; return to the list and choose an item"
                )
            kind = pair[0]
            status = str(form.get("status") or "").strip()
            if not status:
                raise ValueError("status is required; choose a progress status and try again")
            if kind is ExecutionSubjectKind.MANUFACTURER_INPUT:
                raise ValueError(
                    "manufacturer-input status comes from verification; change it on the "
                    "manufacturer-requirement screen"
                )
            if kind is ExecutionSubjectKind.RETURNABLE:
                changes: dict[str, object] = {
                    "expected_version": int(str(form.get("expected_version", "0"))),
                    "status": status,
                }
                if status == "Blocked":
                    changes["blocked_reason"] = str(form.get("blocked_reason") or "")
                elif status == "Complete":
                    changes.update(
                        completion_evidence=str(form.get("completion_evidence") or ""),
                        evidence_document_version_id=resolve_document(
                            bid_id, str(form.get("evidence_document") or "")
                        ),
                    )
                elif status == "Not required":
                    changes["note"] = str(form.get("not_required_reason") or "")
                result = action_service.update_returnable(bid_id, subject_id, changes, actor)
            else:
                action_changes: dict[str, object] = {
                    "expected_version": int(str(form.get("expected_version", "0"))),
                    "status": status,
                }
                if status == "Blocked":
                    action_changes.update(
                        blocker_description=str(form.get("blocker_description") or ""),
                        resolution_owner=str(form.get("resolution_owner") or ""),
                    )
                elif status == "Complete":
                    action_changes.update(
                        completion_outcome=str(form.get("completion_outcome") or ""),
                        completion_evidence=str(form.get("completion_evidence") or ""),
                    )
                elif status == "Not required":
                    action_changes["cancellation_reason"] = str(
                        form.get("cancellation_reason") or ""
                    )
                result = action_service.update_action(bid_id, subject_id, action_changes, actor)
        except (ValidationError, ValueError) as exc:
            return item_response(
                request,
                bid_id,
                kind_slug,
                subject_id,
                status_code=422,
                entered=entered,
                active_form="progress",
                form_error=_exception_message(exc),
            )
        return redirect_result(bid_id, str(form.get("return_to") or "") or None, fallback, result)

    @app.post("/bids/{bid_id}/execution/items/{kind_slug}/{subject_id}/notes")
    async def add_execution_note(
        request: Request, bid_id: str, kind_slug: str, subject_id: str
    ) -> Response:
        form = await request.form()
        entered: dict[str, object] = {str(key): value for key, value in form.items()}
        fallback = f"/bids/{quote(bid_id)}/execution/items/{kind_slug}/{quote(subject_id)}"
        try:
            pair = _ITEM_KINDS.get(kind_slug)
            if pair is None:
                raise ValueError(
                    "execution item type was not found; return to the list and choose an item"
                )
            result = action_service.add_note(
                bid_id, pair[0], subject_id, str(form.get("body") or ""), actor
            )
        except (ValidationError, ValueError) as exc:
            return item_response(
                request,
                bid_id,
                kind_slug,
                subject_id,
                status_code=422,
                entered=entered,
                active_form="note",
                form_error=_exception_message(exc),
            )
        return redirect_result(bid_id, str(form.get("return_to") or "") or None, fallback, result)

    @app.post("/bids/{bid_id}/execution/items/{kind_slug}/{subject_id}/prerequisites")
    async def add_execution_prerequisite(
        request: Request, bid_id: str, kind_slug: str, subject_id: str
    ) -> Response:
        form = await request.form()
        entered: dict[str, object] = {str(key): value for key, value in form.items()}
        fallback = f"/bids/{quote(bid_id)}/execution/items/{kind_slug}/{quote(subject_id)}"
        try:
            pair = _ITEM_KINDS.get(kind_slug)
            if pair is None:
                raise ValueError(
                    "execution item type was not found; return to the list and choose an item"
                )
            prerequisite = resolve_candidate(bid_id, str(form.get("prerequisite") or ""))
            if prerequisite is None:
                raise ValueError("no prerequisite was selected; choose another item from this Bid")
            result = action_service.add_prerequisite(
                bid_id,
                pair[0],
                subject_id,
                prerequisite[0],
                prerequisite[1],
                str(form.get("note") or "") or None,
                actor,
            )
        except (ValidationError, ValueError) as exc:
            return item_response(
                request,
                bid_id,
                kind_slug,
                subject_id,
                status_code=422,
                entered=entered,
                active_form="prerequisite",
                form_error=_exception_message(exc),
            )
        return redirect_result(bid_id, str(form.get("return_to") or "") or None, fallback, result)

    @app.post(
        "/bids/{bid_id}/execution/items/{kind_slug}/{subject_id}/prerequisites/"
        "{dependency_id}/remove"
    )
    async def remove_execution_prerequisite(
        request: Request,
        bid_id: str,
        kind_slug: str,
        subject_id: str,
        dependency_id: str,
    ) -> Response:
        form = await request.form()
        fallback = f"/bids/{quote(bid_id)}/execution/items/{kind_slug}/{quote(subject_id)}"
        try:
            pair = _ITEM_KINDS.get(kind_slug)
            if pair is None:
                raise ValueError(
                    "execution item type was not found; return to the list and choose an item"
                )
            result = action_service.remove_prerequisite_for_item(
                bid_id, pair[0], subject_id, dependency_id, actor
            )
        except (ValidationError, ValueError) as exc:
            return item_response(
                request,
                bid_id,
                kind_slug,
                subject_id,
                status_code=422,
                active_form="prerequisite",
                form_error=_exception_message(exc),
            )
        return redirect_result(bid_id, str(form.get("return_to") or "") or None, fallback, result)

    @app.post("/bids/{bid_id}/execution/actions")
    async def create_execution_action(request: Request, bid_id: str) -> Response:
        form = await request.form()
        entered: dict[str, object] = {str(key): value for key, value in form.items()}
        fallback = f"/bids/{quote(bid_id)}/execution/actions?state=all"
        try:
            prerequisite = resolve_candidate(bid_id, str(form.get("prerequisite") or ""))
            result = action_service.create_manual_action(
                bid_id,
                str(form.get("title") or ""),
                str(form.get("details") or "") or None,
                str(form.get("responsible") or "") or None,
                _contributors(form.get("contributors")),
                _optional_date(form.get("due_date")),
                _priority(form.get("priority")),
                None,
                prerequisite,
                actor,
            )
        except (ValidationError, ValueError) as exc:
            return list_response(
                request,
                bid_id,
                "actions",
                "all",
                status_code=422,
                entered=entered,
                active_form="add-action",
                form_error=_exception_message(exc),
            )
        return redirect_result(bid_id, str(form.get("return_to") or "") or None, fallback, result)

    def assignment_subject(
        bid_id: str, list_slug: str, token: str
    ) -> tuple[ExecutionSubjectKind, str]:
        kind = {
            "returnables": ExecutionSubjectKind.RETURNABLE,
            "manufacturer-inputs": ExecutionSubjectKind.MANUFACTURER_INPUT,
        }.get(list_slug)
        if kind is None:
            raise ValueError("assignment is not available for this list; return to Bid execution")
        all_rows = [item for item_kind, item in all_items(bid_id) if item_kind is kind]
        if token.startswith("row-"):
            try:
                selected = all_rows[int(token.removeprefix("row-"))]
            except (IndexError, ValueError) as exc:
                raise ValueError(
                    "a selected item is no longer listed; reload and select the items again"
                ) from exc
            return kind, selected.subject_id
        action_service.execution.subject_resolver.resolve(bid_id, kind, token)
        return kind, token

    @app.post("/bids/{bid_id}/execution/{list_slug}/items/{subject_id}/quick-assign")
    async def quick_assign_execution_item(
        request: Request, bid_id: str, list_slug: str, subject_id: str
    ) -> Response:
        form = await request.form()
        entered: dict[str, object] = {str(key): value for key, value in form.items()}
        fallback = f"/bids/{quote(bid_id)}/execution/{list_slug}"
        try:
            kind, _ = assignment_subject(bid_id, list_slug, subject_id)
            changes = {
                "expected_version": int(str(form.get("expected_version", "0"))),
                "responsible": str(form.get("responsible") or ""),
                "internal_due_date": _optional_date(form.get("internal_due_date")),
            }
            if kind is ExecutionSubjectKind.RETURNABLE:
                result = action_service.update_returnable(bid_id, subject_id, changes, actor)
            else:
                result = action_service.update_manufacturer_input(
                    bid_id, subject_id, changes, actor
                )
        except (ValidationError, ValueError) as exc:
            return list_response(
                request,
                bid_id,
                list_slug,
                request.query_params.get("state"),
                status_code=422,
                entered=entered,
                active_form=f"quick-{subject_id}",
                form_error=_exception_message(exc),
            )
        return redirect_result(bid_id, str(form.get("return_to") or "") or None, fallback, result)

    @app.post("/bids/{bid_id}/execution/{list_slug}/assign")
    async def bulk_assign_execution_items(
        request: Request, bid_id: str, list_slug: str
    ) -> Response:
        form = await request.form()
        entered: dict[str, object] = {str(key): value for key, value in form.items()}
        fallback = f"/bids/{quote(bid_id)}/execution/{list_slug}"
        try:
            raw_tokens = [str(value) for value in form.getlist("subject_ids")]
            resolved = [assignment_subject(bid_id, list_slug, token) for token in raw_tokens]
            kind = {
                "returnables": ExecutionSubjectKind.RETURNABLE,
                "manufacturer-inputs": ExecutionSubjectKind.MANUFACTURER_INPUT,
            }.get(list_slug)
            if kind is None:
                raise ValueError(
                    "bulk assignment is not available for this list; return to Bid execution"
                )
            result = action_service.bulk_assign(
                bid_id,
                kind,
                [subject_id for _subject_kind, subject_id in resolved],
                str(form.get("responsible") or ""),
                _optional_date(form.get("internal_due_date")),
                actor,
            )
        except (ValidationError, ValueError) as exc:
            entered["subject_ids"] = [str(value) for value in form.getlist("subject_ids")]
            return list_response(
                request,
                bid_id,
                list_slug,
                request.query_params.get("state"),
                status_code=422,
                entered=entered,
                active_form="bulk-assign",
                form_error=_exception_message(exc),
            )
        return redirect_result(bid_id, str(form.get("return_to") or "") or None, fallback, result)

    @app.post(
        "/bids/{bid_id}/execution/manufacturer-inputs/items/{subject_id}/confirm-source-owner"
    )
    async def confirm_list_source_owner(request: Request, bid_id: str, subject_id: str) -> Response:
        form = await request.form()
        fallback = f"/bids/{quote(bid_id)}/execution/manufacturer-inputs"
        try:
            result = action_service.confirm_source_owner(bid_id, subject_id, actor)
        except (ValidationError, ValueError) as exc:
            return list_response(
                request,
                bid_id,
                "manufacturer-inputs",
                request.query_params.get("state"),
                status_code=422,
                active_form=f"confirm-{subject_id}",
                form_error=_exception_message(exc),
            )
        return redirect_result(bid_id, str(form.get("return_to") or "") or None, fallback, result)

    @app.post("/bids/{bid_id}/execution/manufacturer-inputs/confirm-source-owners")
    async def bulk_confirm_list_source_owners(request: Request, bid_id: str) -> Response:
        form = await request.form()
        fallback = f"/bids/{quote(bid_id)}/execution/manufacturer-inputs"
        raw_tokens = [str(value) for value in form.getlist("source_subject_ids")]
        try:
            resolved = [
                assignment_subject(bid_id, "manufacturer-inputs", token) for token in raw_tokens
            ]
            result = action_service.bulk_confirm_source_owners(
                bid_id,
                [subject_id for _kind, subject_id in resolved],
                actor,
            )
        except (ValidationError, ValueError) as exc:
            return list_response(
                request,
                bid_id,
                "manufacturer-inputs",
                request.query_params.get("state"),
                status_code=422,
                entered={"source_subject_ids": raw_tokens},
                active_form="bulk-confirm",
                form_error=_exception_message(exc),
            )
        return redirect_result(bid_id, str(form.get("return_to") or "") or None, fallback, result)

    @app.post("/bids/{bid_id}/execution/items/manufacturer-input/{subject_id}/confirm-source-owner")
    async def confirm_item_source_owner(request: Request, bid_id: str, subject_id: str) -> Response:
        form = await request.form()
        fallback = (
            f"/bids/{quote(bid_id)}/execution/items/manufacturer-input/"
            f"{_encoded_path_id(subject_id)}"
        )
        try:
            result = action_service.confirm_source_owner(bid_id, subject_id, actor)
        except (ValidationError, ValueError) as exc:
            return item_response(
                request,
                bid_id,
                "manufacturer-input",
                subject_id,
                status_code=422,
                active_form="confirm-source-owner",
                form_error=_exception_message(exc),
            )
        return redirect_result(bid_id, str(form.get("return_to") or "") or None, fallback, result)


__all__ = ["register_bid_execution_routes"]
