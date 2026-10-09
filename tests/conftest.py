import atexit
import os
import shutil
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from core.bid_repository import BidRepository
from core.database import Database
from core.enums import Actor, BidLevel, CustomerType
from core.schemas import Bid, Provenance

# ``app.py`` opens its database and storage roots when it is first imported. A test that
# imports it without pointing those at a temporary location would initialise (and rewrite)
# the repo-local ``data/contractiq.db``. Route every default to a throwaway directory
# before any test module can import ``app``; tests that need their own paths still
# override these with ``monkeypatch.setenv``. Assigned unconditionally so an exported
# real database path in the developer's shell can never be reached by the suite.
_ISOLATED_ROOT = Path(tempfile.mkdtemp(prefix="contractiq-tests-"))
atexit.register(shutil.rmtree, _ISOLATED_ROOT, ignore_errors=True)
for _name, _relative in {
    "CONTRACTIQ_DB_PATH": "contractiq.db",
    "CONTRACTIQ_DOCUMENT_ROOT": "documents",
    "CONTRACTIQ_INTAKE_STORAGE_ROOT": "managed_intake",
    "CONTRACTIQ_PROPOSAL_ARTIFACT_ROOT": "proposal_artifacts",
}.items():
    os.environ[_name] = str(_ISOLATED_ROOT / _relative)
for _legacy in ("CONTRACTIQ_INTAKE_ROOTS", "CONTRACTIQ_INTAKE_SOURCE_ROOTS"):
    os.environ.pop(_legacy, None)


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


@pytest.fixture
def bid_repo(tmp_db: Database) -> BidRepository:
    return BidRepository(tmp_db)
