"""Pure snapshot and additive migration checks for owner decisions."""

import sqlite3
from pathlib import Path

from core.bid_repository import BidRepository
from core.database import Database
from core.owner_decision_evidence import build_owner_decision_evidence_snapshot
from core.owner_decision_repository import OwnerDecisionRepository


def test_snapshot_builder_reports_available_coverage_and_questions() -> None:
    snapshot = build_owner_decision_evidence_snapshot(
        received_files_total=4,
        received_files_reviewed=3,
        latest_discovery_job={
            "job_id": "JOB-1",
            "state": "PARTIAL",
            "files_total": 4,
            "files_done": 4,
            "files_failed": 1,
            "passages_total": 8,
            "passages_analysed": 6,
            "passages_failed": 2,
        },
        discovery_partial_or_failed_files=1,
        requirement_count=0,
        accepted_deliverable_count=0,
    )
    assert snapshot.latest_discovery_job is not None
    assert snapshot.latest_discovery_job.state == "PARTIAL"
    assert len(snapshot.unresolved_questions) == 4


def test_owner_decision_migration_is_idempotent_on_an_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "existing.db"
    database = Database(path)
    BidRepository(database)
    OwnerDecisionRepository(database)
    OwnerDecisionRepository(Database(path))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM bid_owner_decisions").fetchone()[0] == 0
        names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='trigger'")
        }
    assert {"owner_decisions_immutable_update", "owner_decisions_immutable_delete"} <= names
