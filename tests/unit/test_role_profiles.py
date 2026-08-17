from datetime import date

import pytest
from pydantic import ValidationError

from core.role_profiles import RoleProfileCreate


def test_draft_normalizes_content_and_domains_in_authoritative_order() -> None:
    draft = RoleProfileCreate.model_validate(
        {
            "title": "  Bids and Contracts Manager  ",
            "organization": "  Example organization  ",
            "mission": "  Deliver controlled pursuits  ",
            "domains": [
                "ROLE_DEVELOPMENT",
                "CUSTOMER_SOLUTION",
                "ROLE_DEVELOPMENT",
                "STRATEGIC_OPPORTUNITY",
            ],
            "effective_from": "2026-09-01",
        }
    )

    assert draft.title == "Bids and Contracts Manager"
    assert draft.organization == "Example organization"
    assert draft.mission == "Deliver controlled pursuits"
    assert [domain.value for domain in draft.domains] == [
        "STRATEGIC_OPPORTUNITY",
        "CUSTOMER_SOLUTION",
        "ROLE_DEVELOPMENT",
    ]


def test_draft_requires_title_and_valid_window_but_allows_incomplete_content() -> None:
    draft = RoleProfileCreate(title="Role", effective_from=date(2026, 9, 1))
    assert draft.mission == ""
    assert draft.domains == []

    with pytest.raises(ValidationError, match="title must be non-empty"):
        RoleProfileCreate(title="   ", effective_from=date(2026, 9, 1))
    with pytest.raises(ValidationError, match="effective_until must be on or after"):
        RoleProfileCreate(
            title="Role",
            effective_from=date(2026, 9, 2),
            effective_until=date(2026, 9, 1),
        )
    with pytest.raises(ValidationError):
        RoleProfileCreate.model_validate(
            {
                "title": "Role",
                "effective_from": "2026-09-01",
                "domains": ["CUSTOM_DOMAIN"],
            }
        )
