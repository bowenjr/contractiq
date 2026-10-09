"""Plain-business-language presentation of Bid package intake evidence.

The intake evidence model is deliberately technical: it records immutable hashes, classification
methods, eligibility states and relationship kinds. This module translates that evidence into the
words a bid manager uses, and decides which controls are worth showing at all. It never changes
stored evidence and never claims a document's contents were analyzed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.bid_package_intake import (
    AnalysisEligibility,
    ClassificationMethod,
    ContentForm,
    GoverningDisposition,
    ReleaseType,
)

#: Release kinds that can carry customer directives changing an earlier release.
ADDENDUM_RELEASE_TYPES = frozenset(
    {
        ReleaseType.ADDENDUM.value,
        ReleaseType.CLARIFICATION.value,
        ReleaseType.REVISED_PACKAGE.value,
        ReleaseType.OTHER.value,
    }
)

#: Classification methods that represent a machine proposal a human has not yet confirmed.
PROPOSED_CLASSIFICATION_METHODS = frozenset(
    {
        ClassificationMethod.LOCAL_RULE.value,
        ClassificationMethod.LOCAL_AI_PROPOSAL.value,
        ClassificationMethod.IMPORTED_AI_PROPOSAL.value,
    }
)

#: Customer directive vocabulary, in Jason's language, stored as stable codes.
DIRECTIVE_TYPE_CHOICES: tuple[tuple[str, str], ...] = (
    ("NEW_DOCUMENT", "New document"),
    ("REPLACES_DOCUMENT", "Replaces earlier document"),
    ("CHANGES_DOCUMENT", "Changes part of earlier document"),
    ("WITHDRAWS_DOCUMENT", "Withdraws earlier document"),
    ("NO_BID_IMPACT", "No Bid impact"),
    ("NEEDS_REVIEW", "Needs review"),
)

_DIRECTIVE_TYPE_LABELS = dict(DIRECTIVE_TYPE_CHOICES)

_CONTENT_FORM_LABELS = {
    ContentForm.TEXTUAL.value: "Text document",
    ContentForm.DRAWING.value: "Drawing",
    ContentForm.MIXED.value: "Text and drawings",
    ContentForm.UNKNOWN.value: "Kind not yet identified",
}

_ELIGIBILITY_LABELS = {
    AnalysisEligibility.NOT_ASSESSED.value: "Not yet decided",
    AnalysisEligibility.ELIGIBLE.value: "Include for analysis",
    AnalysisEligibility.EXCLUDED.value: "Exclude",
}

_GOVERNING_LABELS = {
    GoverningDisposition.GOVERNING.value: "Governing Bid Basis source",
    GoverningDisposition.NOT_GOVERNING.value: "Not a governing source",
    GoverningDisposition.UNSET.value: "Not selected as governing",
}

RELEASE_REVIEW_NOTICE_CODES = frozenset(
    {
        "received",
        "analysis_included",
        "analysis_excluded",
        "analysis_undecided",
        "governing_selected",
        "governing_removed",
        "none_selected",
    }
)


def release_review_notice(
    code: str, count: int, review_remaining: int, changed: int | None = None
) -> str | None:
    """Render a whitelisted release-review result without reflecting query-string text."""
    if changed is not None:
        # (verb, tail, already-state) per code; governing codes omit the review-remaining sentence.
        changed_parts = {
            "analysis_included": ("Included", " for analysis", "Included for analysis"),
            "analysis_excluded": ("Excluded", " from analysis", "Excluded from analysis"),
            "analysis_undecided": ("Marked", " as Not yet decided", "Not yet decided"),
            "governing_selected": (
                "Selected",
                " as governing Bid Basis sources",
                "selected as governing Bid Basis sources",
            ),
            "governing_removed": (
                "Removed",
                " from the governing Bid Basis sources",
                "not governing Bid Basis sources",
            ),
        }
        parts = changed_parts.get(code)
        if parts is not None:
            verb, tail, state = parts
            if changed == 0:
                message = f"No change: all {count} selected file(s) were already {state}."
            else:
                message = (
                    f"{verb} {count} file(s){tail}: {changed} changed, "
                    f"{count - changed} already set."
                )
            if code in {"analysis_included", "analysis_excluded", "analysis_undecided"}:
                message = f"{message} {review_remaining} file(s) still need a review decision."
            return message
    messages = {
        "received": (
            f"Received {count} file(s) into managed storage. "
            "Now choose which files ContractIQ may analyse."
        ),
        "analysis_included": (
            f"Included {count} file(s) for analysis. "
            f"{review_remaining} file(s) still need a review decision."
        ),
        "analysis_excluded": (
            f"Excluded {count} file(s) from analysis. "
            f"{review_remaining} file(s) still need a review decision."
        ),
        "analysis_undecided": (
            f"Marked {count} file(s) as Not yet decided. "
            f"{review_remaining} file(s) still need a review decision."
        ),
        "governing_selected": (f"Selected {count} file(s) as governing Bid Basis sources."),
        "governing_removed": (f"Removed {count} file(s) from the governing Bid Basis sources."),
        "none_selected": (
            "No files were selected. Tick one or more files, or use "
            "'Select all files in this folder', then try again."
        ),
    }
    return messages.get(code)


def human_bytes(value: int | float | str | None) -> str:
    """Render a byte count the way a person reads a file size."""
    try:
        size = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "Unknown size"
    if size < 0:
        return "Unknown size"
    if size < 1024:
        return f"{int(size)} bytes"
    for unit in ("KB", "MB", "GB"):
        size /= 1024
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
    return f"{size:.1f} GB"


def directive_type_label(code: str | None) -> str:
    """Return the business wording for a directive code, tolerating historical free text."""
    if not code:
        return "Needs review"
    return _DIRECTIVE_TYPE_LABELS.get(code, code.replace("_", " ").capitalize())


def content_form_label(value: str | None) -> str:
    """Return the business wording for a recorded content form."""
    return _CONTENT_FORM_LABELS.get(str(value or ""), "Kind not yet identified")


def eligibility_label(value: str | None) -> str:
    """Return the business wording for a recorded analysis-inclusion decision."""
    return _ELIGIBILITY_LABELS.get(str(value or ""), "Not yet reviewed")


def review_status_label(file_row: dict[str, Any]) -> str:
    """Summarize one received file's review state in one plain sentence fragment."""
    eligibility = str(file_row.get("analysis_eligibility") or "")
    if eligibility == AnalysisEligibility.NOT_ASSESSED.value:
        return "Not yet decided"
    form = content_form_label(file_row.get("content_form"))
    return f"{eligibility_label(eligibility)} · {form}"


def is_addendum_release(release_type: str | None) -> bool:
    """Return whether directives about an earlier release can apply to this release kind."""
    return str(release_type or "") in ADDENDUM_RELEASE_TYPES


def shows_confidence(file_row: dict[str, Any]) -> bool:
    """Return whether a confidence figure is meaningful for this file.

    Confidence belongs to an automated proposal. A safe default and a human review have nothing to
    be confident about, so the control is hidden rather than asked for.
    """
    if file_row.get("confidence") is not None:
        return True
    return str(file_row.get("classification_method") or "") in PROPOSED_CLASSIFICATION_METHODS


def proposed_document_title(original_filename: str) -> str:
    """Propose a controlled-document title from the received filename.

    This reads the name the customer gave the file. It does not read the document.
    """
    stem = original_filename.rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    cleaned = stem.replace("_", " ").strip()
    return (cleaned or original_filename)[:300]


def decorate_release(release: dict[str, Any]) -> dict[str, Any]:
    """Add presentation fields for the two independent received-file decisions.

    Include/Exclude decides which files analysis and deliverable discovery may use. Separately,
    an Included file can be selected as a governing Bid Basis source; only such a source needs an
    exact controlled-version link before the Bid Basis is ready. Stored evidence is never changed.
    """
    decorated = dict(release)
    files = [dict(item) for item in release.get("files") or []]
    digests: dict[tuple[str, int], int] = {}
    for item in files:
        key = (str(item.get("sha256")), int(item.get("byte_size") or 0))
        digests[key] = digests.get(key, 0) + 1
    unreviewed: list[dict[str, str]] = []
    governing_candidates: list[dict[str, object]] = []
    governing_unlinked: list[dict[str, str]] = []
    included_count = 0
    governing_selected_count = 0
    for item in files:
        key = (str(item.get("sha256")), int(item.get("byte_size") or 0))
        item["size_label"] = human_bytes(item.get("byte_size"))
        item["review_status"] = review_status_label(item)
        item["show_confidence"] = shows_confidence(item)
        item["is_excluded"] = (
            str(item.get("analysis_eligibility") or "") == AnalysisEligibility.EXCLUDED.value
        )
        item["has_duplicate"] = digests[key] > 1
        item["proposed_title"] = proposed_document_title(str(item.get("original_filename") or ""))
        governing_disposition = str(
            item.get("governing_disposition") or GoverningDisposition.UNSET.value
        )
        if governing_disposition not in _GOVERNING_LABELS:
            governing_disposition = GoverningDisposition.UNSET.value
        item["governing_disposition"] = governing_disposition
        item["governing_label"] = _GOVERNING_LABELS[governing_disposition]
        item["is_governing"] = governing_disposition == GoverningDisposition.GOVERNING.value
        path = str(item.get("original_relative_path") or item.get("original_filename") or "")
        file_id = str(item.get("file_id") or "")
        eligibility = str(item.get("analysis_eligibility") or "")
        if eligibility == AnalysisEligibility.NOT_ASSESSED.value:
            unreviewed.append({"file_id": file_id, "path": path})
        elif eligibility == AnalysisEligibility.ELIGIBLE.value:
            included_count += 1
            if item["is_governing"]:
                governing_selected_count += 1
                if not item.get("document_version_id"):
                    governing_unlinked.append(
                        {"file_id": file_id, "path": path, "proposed_title": item["proposed_title"]}
                    )
            else:
                governing_candidates.append(
                    {
                        "file_id": file_id,
                        "path": path,
                        "governing_event_id": item.get("governing_event_id"),
                    }
                )
    decorated["files"] = files
    decorated["file_groups"] = group_received_files(files)
    decorated["is_addendum"] = is_addendum_release(release.get("release_type"))
    decorated["file_count"] = len(files)
    decorated["unreviewed_files"] = unreviewed
    decorated["governing_candidates"] = governing_candidates
    decorated["governing_unlinked_files"] = governing_unlinked
    # Compatibility for the existing intake template until its governing controls are migrated.
    decorated["eligible_unlinked_files"] = governing_unlinked
    decorated["outstanding_summary"] = outstanding_summary(
        len(files),
        unreviewed,
        governing_unlinked,
        included_count=included_count,
        governing_selected_count=governing_selected_count,
    )
    return decorated


def group_received_files(files: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Group file evidence by its customer's top-level folder, in a stable display order.

    A received relative path is evidence, not a filesystem path to open.  This projection only
    splits its slash-separated display components and keeps unusual values as literal text for
    the template to escape.  Root-level files form their own explicit group.
    """
    buckets: dict[tuple[bool, str], list[dict[str, Any]]] = {}
    for file_row in files:
        path = str(
            file_row.get("original_relative_path") or file_row.get("original_filename") or ""
        )
        parts = [part for part in path.split("/") if part and part != "."]
        top_folder = parts[0] if len(parts) > 1 else ""
        key = (not bool(top_folder), top_folder)
        buckets.setdefault(key, []).append(file_row)

    groups: list[dict[str, Any]] = []
    first_action_group: int | None = None
    for root_files, top_folder in sorted(
        buckets,
        key=lambda item: (0 if item[0] else 1, item[1].casefold(), item[1]),
    ):
        group_files = sorted(
            buckets[(root_files, top_folder)],
            key=lambda item: (
                str(item.get("original_relative_path") or "").casefold(),
                str(item.get("original_relative_path") or ""),
                str(item.get("file_id") or ""),
            ),
        )
        awaiting_review = sum(
            str(item.get("analysis_eligibility") or "") == AnalysisEligibility.NOT_ASSESSED.value
            for item in group_files
        )
        included = sum(
            str(item.get("analysis_eligibility") or "") == AnalysisEligibility.ELIGIBLE.value
            for item in group_files
        )
        excluded = sum(
            str(item.get("analysis_eligibility") or "") == AnalysisEligibility.EXCLUDED.value
            for item in group_files
        )
        under_control = sum(bool(item.get("document_version_id")) for item in group_files)
        requires_action = awaiting_review > 0 or included > under_control
        if requires_action and first_action_group is None:
            first_action_group = len(groups)
        groups.append(
            {
                "label": "Files at package root" if root_files else top_folder,
                "is_root": root_files,
                "files": group_files,
                "file_count": len(group_files),
                "awaiting_review": awaiting_review,
                "included": included,
                "excluded": excluded,
                "under_control": under_control,
                "requires_action": requires_action,
                "open": False,
            }
        )
    if first_action_group is not None:
        groups[first_action_group]["open"] = True
    return tuple(groups)


def outstanding_summary(
    file_count: int,
    unreviewed: Sequence[object],
    governing_unlinked: Sequence[object],
    *,
    included_count: int = 0,
    governing_selected_count: int = 0,
) -> dict[str, object]:
    """Report the independent analysis-discovery and governing-Bid-Basis readiness facts.

    Discovery is ready as soon as at least one file is Included; undecided files remain outside
    analysis until separately Included. Bid Basis confirmation still needs every file reviewed,
    at least one Included file explicitly selected as governing, and every selected governing
    source linked to an exact controlled version
    (`BidPackageRepository._release_incorporation_issues` is the actual control gate).
    """
    all_reviewed = not unreviewed
    return {
        "review_total": file_count,
        "review_remaining": len(unreviewed),
        "all_reviewed": all_reviewed,
        "governing_sources_pending": len(governing_unlinked),
        "all_governing_sources_controlled": not governing_unlinked,
        "included_count": included_count,
        "governing_selected_count": governing_selected_count,
        "discovery_ready": included_count > 0,
        "basis_ready": (all_reviewed and governing_selected_count > 0 and not governing_unlinked),
    }


__all__ = [
    "ADDENDUM_RELEASE_TYPES",
    "DIRECTIVE_TYPE_CHOICES",
    "RELEASE_REVIEW_NOTICE_CODES",
    "content_form_label",
    "decorate_release",
    "directive_type_label",
    "eligibility_label",
    "human_bytes",
    "group_received_files",
    "is_addendum_release",
    "outstanding_summary",
    "release_review_notice",
    "proposed_document_title",
    "review_status_label",
    "shows_confidence",
]
