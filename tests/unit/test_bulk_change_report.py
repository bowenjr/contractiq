"""Bulk review notices report files actually changed separately from selections."""

import pytest

from core.bid_package_presentation import release_review_notice


@pytest.mark.parametrize(
    "code",
    ["analysis_included", "analysis_excluded", "analysis_undecided"],
)
def test_analysis_notice_reports_changed_and_already_set(code: str) -> None:
    message = release_review_notice(code, 5, 2, changed=3)
    assert "5 file(s)" in message
    assert "3 changed, 2 already set." in message
    assert "2 file(s) still need a review decision." in message


@pytest.mark.parametrize("code", ["analysis_included", "analysis_excluded", "analysis_undecided"])
def test_analysis_notice_no_change(code: str) -> None:
    message = release_review_notice(code, 4, 1, changed=0)
    assert message.startswith("No change: all 4 selected file(s) were already ")
    assert "1 file(s) still need a review decision." in message


@pytest.mark.parametrize("code", ["governing_selected", "governing_removed"])
def test_governing_notice_changed_and_no_change(code: str) -> None:
    partial = release_review_notice(code, 3, 0, changed=1)
    assert "1 changed, 2 already set." in partial
    assert "review decision" not in partial
    none = release_review_notice(code, 3, 0, changed=0)
    assert none.startswith("No change: all 3 selected file(s) were already ")
    assert "review decision" not in none


def test_notice_without_changed_keeps_legacy_message() -> None:
    message = release_review_notice("analysis_included", 5, 0)
    assert message == "Included 5 file(s) for analysis. 0 file(s) still need a review decision."
