from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from core.negotiation import (
    Concession,
    Mandate,
    NegotiationIssue,
    Priority,
    validate_concession,
)


def mandate() -> Mandate:
    now = datetime.now(UTC)
    return Mandate(
        plan_version_id="V",
        bid_id="B",
        authorized_actors=("actor",),
        allowed_actions=("OFFER",),
        issue_codes=("I",),
        limit_amount=Decimal("10"),
        currency="CAD",
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(minutes=1),
        state="AUTHORIZED",
    )


def test_must_change_requires_fallback() -> None:
    with pytest.raises(ValueError):
        NegotiationIssue(
            plan_id="P",
            code="I",
            priority=Priority.MUST_CHANGE,
            owner="o",
            customer_current="x",
            opening="x",
            target="x",
            fallback_minimum="",
            walk_away_or_escalate="",
            rationale="x",
        )


def test_concession_requires_explicit_current_mandate() -> None:
    value = Concession(
        bid_id="B",
        issue_code="I",
        version_number=1,
        amount=Decimal("2"),
        currency="CAD",
        unit="TOTAL",
        basis="SYNTHETIC",
        created_at=datetime.now(UTC),
    )
    validate_concession(value, mandate(), "actor", datetime.now(UTC))
    with pytest.raises(ValueError):
        validate_concession(value, None, "actor", datetime.now(UTC))
