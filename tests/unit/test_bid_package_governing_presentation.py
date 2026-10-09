"""Presentation tests for analysis inclusion and governing-source selection."""

from __future__ import annotations

from core.bid_package_presentation import decorate_release, eligibility_label


def _file(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "file_id": "RF-1",
        "original_relative_path": "Part A/requirements.pdf",
        "original_filename": "requirements.pdf",
        "sha256": "a" * 64,
        "byte_size": 2048,
        "content_form": "TEXTUAL",
        "classification_method": "HUMAN_REVIEW",
        "confidence": None,
        "analysis_eligibility": "ELIGIBLE",
        "exclusion_reason": None,
        "duplicate_of_file_id": None,
        "governing_event_id": None,
        "governing_disposition": "UNSET",
        "document_version_id": None,
    }
    row.update(overrides)
    return row


def test_each_file_has_plain_language_governing_state() -> None:
    release = decorate_release(
        {
            "release_type": "INITIAL_PACKAGE",
            "files": [
                _file(file_id="RF-1"),
                _file(
                    file_id="RF-2",
                    original_relative_path="Part A/schedule.pdf",
                    governing_event_id="GSE-2",
                    governing_disposition="GOVERNING",
                ),
                _file(
                    file_id="RF-3",
                    original_relative_path="Part A/readme.pdf",
                    governing_event_id="GSE-3",
                    governing_disposition="NOT_GOVERNING",
                ),
            ],
        }
    )

    assert [
        (item["governing_disposition"], item["governing_label"], item["is_governing"])
        for item in release["files"]
    ] == [
        ("UNSET", "Not selected as governing", False),
        ("GOVERNING", "Governing Bid Basis source", True),
        ("NOT_GOVERNING", "Not a governing source", False),
    ]


def test_candidates_and_unlinked_lists_represent_different_next_actions() -> None:
    release = decorate_release(
        {
            "release_type": "INITIAL_PACKAGE",
            "files": [
                _file(file_id="RF-candidate"),
                _file(
                    file_id="RF-declined",
                    original_relative_path="Part A/declined.pdf",
                    governing_event_id="GSE-declined",
                    governing_disposition="NOT_GOVERNING",
                ),
                _file(
                    file_id="RF-unlinked",
                    original_relative_path="Part A/unlinked_source.pdf",
                    original_filename="unlinked_source.pdf",
                    governing_event_id="GSE-unlinked",
                    governing_disposition="GOVERNING",
                ),
                _file(
                    file_id="RF-controlled",
                    original_relative_path="Part A/controlled.pdf",
                    governing_event_id="GSE-controlled",
                    governing_disposition="GOVERNING",
                    document_version_id="DV-1",
                ),
                _file(
                    file_id="RF-excluded",
                    analysis_eligibility="EXCLUDED",
                    exclusion_reason="Administrative",
                ),
            ],
        }
    )

    assert release["governing_candidates"] == [
        {
            "file_id": "RF-candidate",
            "path": "Part A/requirements.pdf",
            "governing_event_id": None,
        },
        {
            "file_id": "RF-declined",
            "path": "Part A/declined.pdf",
            "governing_event_id": "GSE-declined",
        },
    ]
    assert release["governing_unlinked_files"] == [
        {
            "file_id": "RF-unlinked",
            "path": "Part A/unlinked_source.pdf",
            "proposed_title": "unlinked source",
        }
    ]
    assert release["eligible_unlinked_files"] is release["governing_unlinked_files"]


def test_discovery_readiness_does_not_wait_for_a_bid_basis() -> None:
    release = decorate_release({"release_type": "INITIAL_PACKAGE", "files": [_file()]})

    assert release["outstanding_summary"] == {
        "review_total": 1,
        "review_remaining": 0,
        "all_reviewed": True,
        "governing_sources_pending": 0,
        "all_governing_sources_controlled": True,
        "included_count": 1,
        "governing_selected_count": 0,
        "discovery_ready": True,
        "basis_ready": False,
    }


def test_basis_is_not_ready_while_a_governing_source_lacks_a_link() -> None:
    release = decorate_release(
        {
            "release_type": "INITIAL_PACKAGE",
            "files": [_file(governing_disposition="GOVERNING", governing_event_id="GSE-1")],
        }
    )

    summary = release["outstanding_summary"]
    assert summary["discovery_ready"] is True
    assert summary["governing_selected_count"] == 1
    assert summary["governing_sources_pending"] == 1
    assert summary["basis_ready"] is False


def test_basis_is_ready_only_after_review_and_control_are_complete() -> None:
    release = decorate_release(
        {
            "release_type": "INITIAL_PACKAGE",
            "files": [
                _file(
                    governing_disposition="GOVERNING",
                    governing_event_id="GSE-1",
                    document_version_id="DV-1",
                )
            ],
        }
    )

    assert release["outstanding_summary"]["basis_ready"] is True


def test_analysis_decision_label_does_not_describe_bid_basis_selection() -> None:
    assert eligibility_label("ELIGIBLE") == "Include for analysis"
    assert eligibility_label("EXCLUDED") == "Exclude"
