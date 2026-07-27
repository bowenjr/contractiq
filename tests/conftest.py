from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from core.database import Database
from core.enums import Actor, BidLevel, CustomerType
from core.schemas import Bid, Provenance


@pytest.fixture
def valid_provenance() -> Provenance:
    return Provenance(created_by=Actor.SYSTEM)


@pytest.fixture
def valid_bid() -> Bid:
    now = datetime.now(UTC)
    return Bid(
        bid_id="B-2026-0001",
        customer="Example EPC",
        customer_type=CustomerType.EPC,
        project_name="Example Project",
        sales_owner="Sales Owner",
        bc_owner="Bid Coordinator",
        release_date=date(2026, 7, 1),
        customer_due_date=date(2026, 7, 31),
        internal_due_date=date(2026, 7, 28),
        estimated_value=Decimal("1000000"),
        classification=BidLevel.LEVEL_2,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def tmp_db(tmp_path: Path) -> Database:
    return Database(tmp_path / "contractiq-test.db")
