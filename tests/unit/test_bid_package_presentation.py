"""Behavioural tests for the plain-language presentation of Bid package intake."""

from __future__ import annotations

import pytest

from core.bid_package_presentation import (
    DIRECTIVE_TYPE_CHOICES,
    content_form_label,
    decorate_release,
    directive_type_label,
    eligibility_label,
    group_received_files,
    human_bytes,
    is_addendum_release,
    outstanding_summary,
    proposed_document_title,
    shows_confidence,
)


def _file(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "file_id": "RF-0000",
        "original_relative_path": "Part A/letter.pdf",
        "original_filename": "letter.pdf",
        "sha256": "a" * 64,
        "byte_size": 2048,
        "content_form": "UNKNOWN",
        "classification_method": "SAFE_DEFAULT",
        "confidence": None,
        "analysis_eligibility": "NOT_ASSESSED",
        "exclusion_reason": None,
        "duplicate_of_file_id": None,
        "document_version_id": None,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "0 bytes"), (512, "512 bytes"), (2048, "2.0 KB"), (5 * 1024 * 1024, "5.0 MB")],
)
def test_human_bytes_reads_as_a_file_size(value: int, expected: str) -> None:
    assert human_bytes(value) == expected


def test_human_bytes_never_raises_on_unusable_input() -> None:
    assert human_bytes(None) == "Unknown size"
    assert human_bytes("not a number") == "Unknown size"
    assert human_bytes(-1) == "Unknown size"


def test_confidence_is_hidden_for_a_safe_default_and_for_a_human_review() -> None:
    assert shows_confidence(_file()) is False
    assert shows_confidence(_file(classification_method="HUMAN_REVIEW")) is False


def test_confidence_is_shown_only_for_an_automated_proposal() -> None:
    assert shows_confidence(_file(classification_method="LOCAL_AI_PROPOSAL")) is True
    assert shows_confidence(_file(classification_method="LOCAL_RULE")) is True
    # A recorded figure is always shown, whatever produced it.
    assert shows_confidence(_file(classification_method="HUMAN_REVIEW", confidence=0.8)) is True


def test_duplicate_details_appear_only_when_a_duplicate_actually_exists() -> None:
    single = decorate_release({"release_type": "INITIAL_PACKAGE", "files": [_file()]})
    assert single["files"][0]["has_duplicate"] is False

    twins = decorate_release(
        {
            "release_type": "INITIAL_PACKAGE",
            "files": [
                _file(file_id="RF-1"),
                _file(file_id="RF-2", original_relative_path="Part B/copy.pdf"),
            ],
        }
    )
    assert [item["has_duplicate"] for item in twins["files"]] == [True, True]


def test_a_differing_size_is_not_treated_as_a_duplicate() -> None:
    release = decorate_release(
        {
            "release_type": "INITIAL_PACKAGE",
            "files": [_file(file_id="RF-1"), _file(file_id="RF-2", byte_size=4096)],
        }
    )
    assert [item["has_duplicate"] for item in release["files"]] == [False, False]


def test_exclusion_state_is_derived_from_the_recorded_decision() -> None:
    release = decorate_release(
        {
            "release_type": "INITIAL_PACKAGE",
            "files": [
                _file(file_id="RF-1"),
                _file(file_id="RF-2", analysis_eligibility="EXCLUDED", exclusion_reason="Scrap"),
            ],
        }
    )
    assert [item["is_excluded"] for item in release["files"]] == [False, True]


def test_outstanding_work_is_counted_and_never_names_a_record_identifier() -> None:
    release = decorate_release(
        {
            "release_type": "INITIAL_PACKAGE",
            "files": [
                _file(file_id="RF-1"),
                _file(file_id="RF-2", analysis_eligibility="ELIGIBLE"),
                _file(
                    file_id="RF-3",
                    analysis_eligibility="ELIGIBLE",
                    document_version_id="DV-9",
                ),
            ],
        }
    )
    summary = " ".join(release["outstanding_summary"])
    assert "1 of 3 received file(s) have not been reviewed" in summary
    assert "1 reviewed file(s) are not yet under document control" in summary
    for identifier in ("RF-1", "RF-2", "RF-3", "DV-9"):
        assert identifier not in summary


def test_a_fully_reviewed_release_states_nothing_outstanding() -> None:
    release = decorate_release(
        {
            "release_type": "INITIAL_PACKAGE",
            "files": [_file(analysis_eligibility="EXCLUDED", exclusion_reason="Scrap")],
        }
    )
    assert release["outstanding_summary"] == ()
    assert outstanding_summary(0, [], []) == ()


def test_only_an_addendum_class_release_can_carry_directives() -> None:
    assert is_addendum_release("INITIAL_PACKAGE") is False
    for kind in ("ADDENDUM", "CLARIFICATION", "REVISED_PACKAGE", "OTHER"):
        assert is_addendum_release(kind) is True
    initial = decorate_release({"release_type": "INITIAL_PACKAGE", "files": []})
    assert initial["is_addendum"] is False
    assert decorate_release({"release_type": "ADDENDUM", "files": []})["is_addendum"] is True


def test_directive_vocabulary_is_business_language_and_survives_historical_free_text() -> None:
    labels = {label for _code, label in DIRECTIVE_TYPE_CHOICES}
    assert labels == {
        "New document",
        "Replaces earlier document",
        "Changes part of earlier document",
        "Withdraws earlier document",
        "No Bid impact",
        "Needs review",
    }
    assert directive_type_label("REPLACES_DOCUMENT") == "Replaces earlier document"
    # Rows written before this vocabulary existed must still render, never crash.
    assert directive_type_label("CHANGE") == "Change"
    assert directive_type_label(None) == "Needs review"


def test_review_status_never_reads_as_a_system_failure() -> None:
    release = decorate_release({"release_type": "INITIAL_PACKAGE", "files": [_file()]})
    assert release["files"][0]["review_status"] == "Not yet decided"
    assert content_form_label("UNKNOWN") == "Kind not yet identified"
    assert eligibility_label("NOT_ASSESSED") == "Not yet decided"
    reviewed = decorate_release(
        {
            "release_type": "INITIAL_PACKAGE",
            "files": [_file(analysis_eligibility="ELIGIBLE", content_form="DRAWING")],
        }
    )
    assert reviewed["files"][0]["review_status"] == "Include · Drawing"


def test_a_document_title_is_proposed_from_the_filename_only() -> None:
    assert proposed_document_title("SYN-1234-AA-0001_Scope-of-Work.pdf") == (
        "SYN-1234-AA-0001 Scope-of-Work"
    )
    assert proposed_document_title("no-extension") == "no-extension"
    assert proposed_document_title("Part A/nested name.docx") == "nested name"
    assert len(proposed_document_title("x" * 500 + ".pdf")) == 300


def test_decoration_never_mutates_the_evidence_it_was_given() -> None:
    original = {"release_type": "INITIAL_PACKAGE", "files": [_file()]}
    decorate_release(original)
    assert original["files"][0] == _file()
    assert "is_addendum" not in original


def test_files_are_grouped_by_top_level_customer_folder_in_stable_order() -> None:
    groups = group_received_files(
        [
            _file(file_id="RF-3", original_relative_path="Zulu/nested/z.pdf"),
            _file(file_id="RF-2", original_relative_path="Alpha/a.pdf"),
            _file(file_id="RF-1", original_relative_path="root.pdf"),
        ]
    )
    assert [group["label"] for group in groups] == ["Files at package root", "Alpha", "Zulu"]
    assert groups[0]["file_count"] == 1
    assert groups[1]["awaiting_review"] == 1
    assert groups[0]["open"] is True
    assert groups[1]["open"] is False


def test_special_folder_names_are_literal_safe_display_values() -> None:
    groups = group_received_files([_file(original_relative_path="A & B <review>/letter.pdf")])
    assert groups[0]["label"] == "A & B <review>"


def test_group_counts_include_review_and_document_control_statuses() -> None:
    groups = group_received_files(
        [
            _file(file_id="RF-1", original_relative_path="Part/file-1.pdf"),
            _file(
                file_id="RF-2",
                original_relative_path="Part/file-2.pdf",
                analysis_eligibility="ELIGIBLE",
                document_version_id="DV-1",
            ),
            _file(
                file_id="RF-3",
                original_relative_path="Part/file-3.pdf",
                analysis_eligibility="EXCLUDED",
                exclusion_reason="Not used",
            ),
        ]
    )
    assert groups[0] | {"files": []} == {
        "label": "Part",
        "is_root": False,
        "files": [],
        "file_count": 3,
        "awaiting_review": 1,
        "included": 1,
        "excluded": 1,
        "under_control": 1,
        "requires_action": True,
        "open": True,
    }
