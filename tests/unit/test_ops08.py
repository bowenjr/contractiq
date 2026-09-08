from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.bid_repository import BidRepository
from core.database import Database
from core.document_repository import DocumentRepository
from core.ops07w import Ops07WorkflowRepository
from core.ops08 import MIGRATION_ID, Ops08Repository, PositionDisposition, PositionRevision
from core.work_item_repository import WorkItemRepository
from core.work_items import WorkCategory


@pytest.fixture
def repository(tmp_path: Path, valid_bid) -> tuple[Ops08Repository, Database, str]:
    db = Database(tmp_path / "ops08.db")
    bids = BidRepository(db)
    bids.create_bid(valid_bid)
    DocumentRepository(db)
    Ops07WorkflowRepository(db)
    WorkItemRepository(db)
    repo = Ops08Repository(db)
    return repo, db, valid_bid.bid_id


def test_initialization_is_idempotent_and_not_complete(repository) -> None:
    repo, _, bid_id = repository
    first = repo.initialize(bid_id, "Morgan", "jason")
    second = repo.initialize(bid_id, "Morgan", "jason")
    assert first > 25 and second == 0
    assert all(row["disposition"] == "NOT_REVIEWED" for row in repo.workspace(bid_id))
    assert repo.readiness(bid_id)["state"] == "BLOCKED"


def test_customer_and_proposed_positions_are_distinct_and_revision_appends(repository) -> None:
    repo, _, bid_id = repository
    repo.initialize(bid_id, "Morgan", "jason")
    row = repo.workspace(bid_id)[0]
    repo.revise(
        row["position_id"],
        1,
        PositionRevision(
            proposed_position="Milestone billing",
            disposition=PositionDisposition.QUALIFY,
            owner="Morgan",
            rationale="Cash flow",
        ),
        "jason",
    )
    detail = repo.detail(row["position_id"])
    assert detail["customer_position"] == ""
    assert detail["proposed_position"] == "Milestone billing"
    assert detail["current_version"] == 2
    assert len(detail["history"]) == 2


def test_explicit_term_requires_exact_source_and_locator() -> None:
    with pytest.raises(ValidationError, match="exact source version and locator"):
        PositionRevision(customer_position="Net 90", owner="Morgan")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"disposition": "NOT_APPLICABLE", "owner": "M"}, "require a reason"),
        ({"disposition": "QUALIFY", "owner": "M"}, "proposed Bid position"),
        ({"disposition": "REJECT", "owner": "M", "rationale": "No"}, "approver"),
        ({"disposition": "CLARIFICATION_REQUIRED", "owner": "M"}, "next action"),
    ],
)
def test_disposition_rules(payload, message) -> None:
    with pytest.raises(ValidationError, match=message):
        PositionRevision.model_validate(payload)


def test_versions_are_immutable_and_stale_change_has_no_audit(repository) -> None:
    repo, db, bid_id = repository
    repo.initialize(bid_id, "Morgan", "jason")
    row = repo.workspace(bid_id)[0]
    before = len(BidRepository(db).list_audit(bid_id))
    with pytest.raises(ValueError, match="stale"):
        repo.revise(row["position_id"], 0, PositionRevision(owner="Morgan"), "jason")
    assert len(BidRepository(db).list_audit(bid_id)) == before
    with db._conn() as conn:
        version_id = repo.detail(row["position_id"])["position_version_id"]
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE commercial_position_versions SET owner='x' WHERE position_version_id=?",
                (version_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute(
                "DELETE FROM commercial_position_versions WHERE position_version_id=?",
                (version_id,),
            )


def test_topic_bootstrap_and_migration_are_restart_safe(repository) -> None:
    repo, db, _ = repository
    count = len(STANDARD := repo.workspace("missing"))
    assert count == 0 and STANDARD == []
    restarted = Ops08Repository(db)
    with db._conn() as conn:
        assert conn.execute("SELECT count(*) FROM commercial_topics").fetchone()[0] > 25
        assert (
            conn.execute(
                "SELECT count(*) FROM ops08_schema_migrations WHERE migration_id=?", (MIGRATION_ID,)
            ).fetchone()[0]
            == 1
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert restarted is not None


def test_qualifications_export_is_derived_and_formula_safe(repository) -> None:
    repo, _, bid_id = repository
    repo.initialize(bid_id, "Morgan", "jason")
    row = repo.workspace(bid_id)[0]
    repo.revise(
        row["position_id"],
        1,
        PositionRevision(
            proposed_position="=1+1", disposition="QUALIFY", owner="Morgan", rationale="Required"
        ),
        "jason",
    )
    content = repo.qualifications_csv(bid_id)
    assert "'=1+1" in content
    assert content.count("'=1+1") == 1


def test_bulk_owner_assignment_is_atomic_and_version_protected(repository) -> None:
    repo, _, bid_id = repository
    repo.initialize(bid_id, "Initial", "jason")
    rows = repo.workspace(bid_id)[:2]
    assert (
        repo.bulk_assign_owner(bid_id, [(row["position_id"], 1) for row in rows], "Morgan", "jason")
        == 2
    )
    assert all(repo.detail(row["position_id"])["owner"] == "Morgan" for row in rows)
    before = [repo.detail(row["position_id"])["current_version"] for row in rows]
    with pytest.raises(ValueError, match="stale"):
        repo.bulk_assign_owner(
            bid_id,
            [(rows[0]["position_id"], 1), (rows[1]["position_id"], 2)],
            "Taylor",
            "jason",
        )
    assert [repo.detail(row["position_id"])["current_version"] for row in rows] == before


def test_topic_revision_and_retirement_preserve_historical_meaning(repository) -> None:
    repo, db, bid_id = repository
    repo.initialize(bid_id, "Morgan", "jason")
    position = next(row for row in repo.workspace(bid_id) if row["topic_key"] == "PAYMENT_TERMS")
    assert position["label"] == "Payment terms"
    repo.revise_topic(
        "PAYMENT_TERMS",
        1,
        label="Payment and credit terms",
        description="Revised help",
        display_group="Payment",
        display_order=8,
        actor="jason",
    )
    repo.retire_topic("PAYMENT_TERMS")
    assert repo.detail(position["position_id"])["label"] == "Payment terms"
    with db._conn() as conn:
        topic = conn.execute(
            "SELECT active,current_version FROM commercial_topics WHERE topic_key='PAYMENT_TERMS'"
        ).fetchone()
        assert (topic["active"], topic["current_version"]) == (0, 2)
        with pytest.raises(sqlite3.IntegrityError, match="referenced"):
            conn.execute("DELETE FROM commercial_topics WHERE topic_key='PAYMENT_TERMS'")


def test_contextual_work_preserves_current_position_version(repository) -> None:
    repo, db, bid_id = repository
    repo.initialize(bid_id, "Morgan", "jason")
    position = repo.workspace(bid_id)[0]
    item = repo.create_linked_work(
        source_kind="commercial",
        source_id=position["position_id"],
        title="Resolve commercial position",
        purpose="Close the commercial blocker",
        category=WorkCategory.COMMERCIAL_REVIEW,
        actor="jason",
    )
    with db._conn() as conn:
        link = conn.execute(
            "SELECT * FROM commercial_position_work_links WHERE work_item_id=?",
            (item.work_item_id,),
        ).fetchone()
        assert link["bid_id"] == bid_id
        assert link["position_version_id"] == position["position_version_id"]
        assert (
            conn.execute(
                "SELECT count(*) FROM audit_log WHERE action IN "
                "('work_item_created','commercial_position_work_linked') AND bid_id=?",
                (bid_id,),
            ).fetchone()[0]
            == 2
        )
