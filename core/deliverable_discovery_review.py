"""Filtering, ordering and paging of the discovery review queue.

A bid can yield hundreds of findings. This module turns the full list into the slice a reviewer
works on: one review status, optionally one document, in document and page order, one page at a
time. It reads nothing and writes nothing.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

PAGE_SIZE = 100
STATUSES = ("pending", "accepted", "rejected", "deferred", "all")
DEFAULT_STATUS = "pending"
LIFECYCLE_TABS: tuple[tuple[str, str, str], ...] = (
    ("bid", "With the bid", "BID_RETURNABLE"),
    ("pre", "Before bidding", "PRE_BID_ACTION"),
    ("mfr", "From the manufacturer", "MANUFACTURER_INPUT"),
    ("post", "After award", "POST_AWARD"),
    ("cond", "Conditional or optional", "CONDITIONAL"),
    ("na", "Not applicable", "NOT_APPLICABLE"),
    ("review", "Needs review", "NEEDS_REVIEW"),
)
LIFECYCLE_KEYS = tuple(key for key, _, _ in LIFECYCLE_TABS) + ("all",)
DEFAULT_LIFECYCLE = "bid"
GROUPS = ("document", "lifecycle")
_KEY_BY_LIFECYCLE = {value: key for key, _, value in LIFECYCLE_TABS}
_LABEL_BY_KEY = {key: label for key, label, _ in LIFECYCLE_TABS}
_NATURAL = re.compile(r"(\d+)")


@dataclass(frozen=True)
class ReviewView:
    """The slice of the review queue to display, with the counts needed to navigate it."""

    rows: tuple[Mapping[str, Any], ...]
    status: str
    document: str
    page: int
    page_count: int
    matching: int
    counts: Mapping[str, int]
    documents: tuple[dict[str, Any], ...]
    lifecycle: str = "all"
    group: str = "document"
    lifecycle_counts: Mapping[str, Mapping[str, int]] | None = None
    groups: tuple[tuple[str, tuple[Mapping[str, Any], ...]], ...] = ()


def _natural(text: str) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in _NATURAL.split(text)]


def document_ids(row: Mapping[str, Any]) -> set[str]:
    """Every document (received file or controlled version) a finding cites."""
    return {
        str(source.get("received_file_id") or source.get("document_version_id") or "")
        for source in row.get("sources", [])
    } - {""}


def source_sort_key(row: Mapping[str, Any], names: Mapping[str, str]) -> tuple[object, ...]:
    """Order by document name, then page or sheet position, then title."""
    sources = row.get("sources") or [{}]
    first = sources[0]
    file_id = str(first.get("received_file_id") or first.get("document_version_id") or "")
    return (
        _natural(names.get(file_id, file_id)),
        _natural(str(first.get("locator") or "")),
        str(row.get("title") or "").lower(),
    )


def lifecycle_key(row: Mapping[str, Any]) -> str:
    """The tab a finding belongs to; a finding with no lifecycle is one that needs review."""
    return _KEY_BY_LIFECYCLE.get(str(row.get("lifecycle") or ""), "review")


def lifecycle_label(key: str) -> str:
    """The plain label of a lifecycle tab."""
    return _LABEL_BY_KEY.get(key, "All findings")


def build_review_view(
    queue: Sequence[Mapping[str, Any]],
    names: Mapping[str, str],
    status: str = DEFAULT_STATUS,
    document: str = "",
    page: int = 1,
    page_size: int = PAGE_SIZE,
    lifecycle: str = "all",
    group: str = "document",
) -> ReviewView:
    """Select, order and page the queue.

    Unknown status values fall back to ``pending`` and unknown lifecycle or group values to
    ``all`` / ``document``. ``counts`` are the review-status counts inside the chosen lifecycle
    tab; ``lifecycle_counts`` give every tab's pending and total findings, whatever is selected.
    """
    status = status if status in STATUSES else DEFAULT_STATUS
    lifecycle = lifecycle if lifecycle in LIFECYCLE_KEYS else "all"
    group = group if group in GROUPS else "document"

    lifecycle_counts: dict[str, dict[str, int]] = {
        key: {"pending": 0, "total": 0} for key in LIFECYCLE_KEYS
    }
    for row in queue:
        tab = lifecycle_key(row)
        for key in (tab, "all"):
            lifecycle_counts[key]["total"] += 1
            if str(row["current_decision"]).lower() == "pending":
                lifecycle_counts[key]["pending"] += 1
    queue = [row for row in queue if lifecycle == "all" or lifecycle_key(row) == lifecycle]

    counts: Counter[str] = Counter()
    for row in queue:
        counts[str(row["current_decision"]).lower()] += 1
    counts["all"] = len(queue)

    in_status = [
        row for row in queue if status == "all" or row["current_decision"].lower() == status
    ]
    per_document: Counter[str] = Counter()
    for row in in_status:
        for file_id in document_ids(row):
            per_document[file_id] += 1
    documents = tuple(
        {"id": file_id, "name": names.get(file_id, file_id), "count": count}
        for file_id, count in sorted(
            per_document.items(), key=lambda item: _natural(names.get(item[0], item[0]))
        )
    )
    if document and document not in per_document:
        document = ""
    selected = [row for row in in_status if not document or document in document_ids(row)]
    if group == "lifecycle":
        order = {key: index for index, key in enumerate(LIFECYCLE_KEYS)}
        selected.sort(key=lambda row: (order[lifecycle_key(row)], source_sort_key(row, names)))
    else:
        selected.sort(key=lambda row: source_sort_key(row, names))

    page_count = max(1, -(-len(selected) // page_size))
    page = min(max(1, page), page_count)
    window = selected[(page - 1) * page_size : page * page_size]
    return ReviewView(
        rows=tuple(window),
        status=status,
        document=document,
        page=page,
        page_count=page_count,
        matching=len(selected),
        counts=dict(counts),
        documents=documents,
        lifecycle=lifecycle,
        group=group,
        lifecycle_counts=lifecycle_counts,
        groups=_groups(window, names, group),
    )


def _groups(
    rows: Sequence[Mapping[str, Any]], names: Mapping[str, str], group: str
) -> tuple[tuple[str, tuple[Mapping[str, Any], ...]], ...]:
    """Consecutive rows under one heading: the document, or the lifecycle then document."""
    grouped: list[tuple[str, list[Mapping[str, Any]]]] = []
    for row in rows:
        sources = row.get("sources") or [{}]
        first = sources[0]
        file_id = str(first.get("received_file_id") or first.get("document_version_id") or "")
        document = str(names.get(file_id, file_id) or "Document").split("/")[-1]
        heading = document
        if group == "lifecycle":
            heading = f"{lifecycle_label(lifecycle_key(row))} — {document}"
        if not grouped or grouped[-1][0] != heading:
            grouped.append((heading, []))
        grouped[-1][1].append(row)
    return tuple((heading, tuple(items)) for heading, items in grouped)
