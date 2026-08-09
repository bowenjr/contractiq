from datetime import UTC, datetime

import pytest

from core.bid_repository import BidRepository
from core.database import Database
from core.ops_foundation import RESPONSIBILITY_DOMAINS, WORK_CATEGORIES, OpsFoundationRepository
from core.work_item_repository import WorkItemRepository
from core.work_item_service import WorkItemService


def test_ops_taxonomy_and_unassigned_capture(tmp_path) -> None:
    db = Database(tmp_path / "ops.db")
    bids = BidRepository(db)
    work = WorkItemRepository(db)
    service = WorkItemService(work, bids, now_factory=lambda: datetime(2026, 8, 9, tzinfo=UTC))
    item = service.create_work_item(
        {"title": "Capture", "category": "CUSTOMER_REQUEST", "next_action_date": "2026-08-09"},
        "synthetic",
    )
    assert item.bid_id is None
    assert item.category.value in WORK_CATEGORIES
    assert len(RESPONSIBILITY_DOMAINS) == 13


def test_role_profile_setup_publish_and_overlap_rejection(tmp_path) -> None:
    db = Database(tmp_path / "ops.db")
    repo = OpsFoundationRepository(db)
    profile = repo.create_profile(
        {
            "version_number": 1,
            "mission": "Synthetic",
            "boundaries": "",
            "coordination": "",
            "outcomes": "",
            "cadence": "",
            "domains": [RESPONSIBILITY_DOMAINS[0]],
            "effective_from": "2026-01-01",
            "created_by": "synthetic",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    repo.publish_profile(profile)
    assert repo.effective_profile() is not None
    with pytest.raises(ValueError):
        repo.create_profile(
            {
                "version_number": 2,
                "mission": "Overlap",
                "boundaries": "",
                "coordination": "",
                "outcomes": "",
                "cadence": "",
                "domains": [],
                "effective_from": "2026-06-01",
                "state": "PUBLISHED",
                "created_by": "synthetic",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        )
