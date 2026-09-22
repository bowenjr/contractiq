"""Plain-business-language presentation of Bid package intake evidence.

The intake evidence model is deliberately technical: it records immutable hashes, classification
methods, eligibility states and relationship kinds. This module translates that evidence into the
words a bid manager uses, and decides which controls are worth showing at all. It never changes
stored evidence and never claims a document's contents were analyzed.
"""

from __future__ import annotations

from typing import Any

from core.bid_package_intake import (
    AnalysisEligibility,
    ClassificationMethod,
    ContentForm,
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
    AnalysisEligibility.ELIGIBLE.value: "Include",
    AnalysisEligibility.EXCLUDED.value: "Exclude",
}


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
    """Return the business wording for a recorded Bid basis review decision."""
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
    """Return the release with presentation fields added; stored evidence is never changed."""
    decorated = dict(release)
    files = [dict(item) for item in release.get("files") or []]
    digests: dict[tuple[str, int], int] = {}
    for item in files:
        key = (str(item.get("sha256")), int(item.get("byte_size") or 0))
        digests[key] = digests.get(key, 0) + 1
    unreviewed: list[str] = []
    eligible_unlinked: list[str] = []
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
        path = str(item.get("original_relative_path") or item.get("original_filename") or "")
        eligibility = str(item.get("analysis_eligibility") or "")
        if eligibility == AnalysisEligibility.NOT_ASSESSED.value:
            unreviewed.append(path)
        elif eligibility == AnalysisEligibility.ELIGIBLE.value and not item.get(
            "document_version_id"
        ):
            eligible_unlinked.append(path)
    decorated["files"] = files
    decorated["file_groups"] = group_received_files(files)
    decorated["is_addendum"] = is_addendum_release(release.get("release_type"))
    decorated["file_count"] = len(files)
    decorated["unreviewed_files"] = unreviewed
    decorated["eligible_unlinked_files"] = eligible_unlinked
    decorated["outstanding_summary"] = outstanding_summary(
        len(files), unreviewed, eligible_unlinked
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
    unreviewed: list[str],
    eligible_unlinked: list[str],
) -> tuple[str, ...]:
    """State what stands between this release and the Bid basis, without record identifiers."""
    lines: list[str] = []
    if unreviewed:
        lines.append(
            f"{len(unreviewed)} of {file_count} received file(s) have not been reviewed yet. "
            "Each one is marked below."
        )
    if eligible_unlinked:
        lines.append(
            f"{len(eligible_unlinked)} reviewed file(s) are not yet under document control. "
            "Use 'Put under document control' on each one."
        )
    return tuple(lines)


__all__ = [
    "ADDENDUM_RELEASE_TYPES",
    "DIRECTIVE_TYPE_CHOICES",
    "content_form_label",
    "decorate_release",
    "directive_type_label",
    "eligibility_label",
    "human_bytes",
    "group_received_files",
    "is_addendum_release",
    "outstanding_summary",
    "proposed_document_title",
    "review_status_label",
    "shows_confidence",
]
