"""Stage 3.1 ownership separation, confirmation, migration, and HTTP coverage."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from core.bid_execution import ExecutionSubjectKind, ResponsibleBasis
from core.bid_execution_dashboard import ExecutionDashboardService
from core.bid_execution_readiness import evaluate_submission_readiness
from core.bid_execution_readiness_models import ReadinessFacts
from core.bid_execution_repository import BidExecutionRepository
from core.database import Database
from core.schemas import Bid
from core.vendor_document_control import CustomerRequirementCreate
from tests.unit.test_stage3_actions import (
    AS_OF,
    ActionContext,
    _context,
    _manufacturer,
)
from tests.unit.test_stage3_actions import context as _action_context_fixture
from tests.unit.test_stage3_ui_forms import (
    _get,
    _post,
    _post_pairs,
)
from tests.unit.test_stage3_ui_forms import forms_app as _forms_app_fixture


@pytest.fixture
def ownership_context(tmp_db: Database, valid_bid: Bid) -> ActionContext:
    """Reuse the Stage 3 action-service fixture with an ownership-specific name."""
    return _action_context_fixture.__wrapped__(tmp_db, valid_bid)


@pytest.fixture
def ownership_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid_bid: Bid
) -> tuple[ModuleType, dict[str, str]]:
    """Reuse the socketless Stage 3 app fixture for ownership HTTP tests."""
    return _forms_app_fixture.__wrapped__(tmp_path, monkeypatch, valid_bid)


def _audit_count(context: ActionContext) -> int:
    with context.db._conn() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action='bid_execution_record_saved'"
            ).fetchone()[0]
        )


def test_inherited_manufacturer_owners_are_unassigned_until_confirmed(
    ownership_context: ActionContext,
) -> None:
    requirement_ids = [_manufacturer(ownership_context, f"Input {number}") for number in range(4)]

    rows = ownership_context.projection.items(
        ownership_context.bid.bid_id, "MANUFACTURER_INPUT", "all", AS_OF
    )

    assert {row.subject_id for row in rows} == set(requirement_ids)
    assert all(row.responsible is None and row.unassigned for row in rows)
    assert all(row.source_owner == "Vendor lead" for row in rows)
    assert all(row.responsible_origin is None for row in rows)


def test_confirm_preserves_register_owner_and_records_stable_provenance(
    ownership_context: ActionContext,
) -> None:
    subject_id = _manufacturer(ownership_context, "Confirmed drawing")
    before_requirement = ownership_context.vendors.get_requirement(subject_id)
    before_audits = _audit_count(ownership_context)

    result = ownership_context.actions.confirm_source_owner(
        ownership_context.bid.bid_id, subject_id, "jason"
    )
    record = ownership_context.execution_service.get_record(
        ownership_context.bid.bid_id, ExecutionSubjectKind.MANUFACTURER_INPUT, subject_id
    )
    projected = ownership_context.projection.items(
        ownership_context.bid.bid_id, "MANUFACTURER_INPUT", "all", AS_OF
    )[0]
    after_requirement = ownership_context.vendors.get_requirement(subject_id)

    assert result.changed
    assert record is not None
    assert record.responsible == "Vendor lead"
    assert record.responsible_basis is ResponsibleBasis.CONFIRMED_SOURCE_OWNER
    assert record.responsible_source_owner == "Vendor lead"
    assert record.responsible_set_by == "jason"
    assert record.responsible_set_at is not None
    assert projected.responsible_origin == "confirmed" and not projected.unassigned
    assert before_requirement == after_requirement
    assert _audit_count(ownership_context) == before_audits + 1

    reloaded = _context(ownership_context.db, ownership_context.bid, create_bid=False)
    persisted = reloaded.execution_service.get_record(
        ownership_context.bid.bid_id, ExecutionSubjectKind.MANUFACTURER_INPUT, subject_id
    )
    assert persisted == record

    repeated = ownership_context.actions.confirm_source_owner(
        ownership_context.bid.bid_id, subject_id, "jason"
    )
    unchanged = ownership_context.execution_service.get_record(
        ownership_context.bid.bid_id, ExecutionSubjectKind.MANUFACTURER_INPUT, subject_id
    )
    assert not repeated.changed and repeated.reused
    assert unchanged == record
    assert _audit_count(ownership_context) == before_audits + 1


def test_confirmation_changes_unassigned_dashboard_and_readiness_counts(
    ownership_context: ActionContext,
) -> None:
    subject_id = _manufacturer(ownership_context, "Readiness drawing")
    dashboard = ExecutionDashboardService(
        ownership_context.projection, ownership_context.bids, ownership_context.deliverables
    )
    before_items = dashboard.filtered_list(
        ownership_context.bid.bid_id, "manufacturer-inputs", "unassigned", AS_OF
    )
    before = evaluate_submission_readiness(
        ReadinessFacts(
            bid_id=ownership_context.bid.bid_id,
            manufacturer_inputs=ownership_context.projection.items(
                ownership_context.bid.bid_id, "MANUFACTURER_INPUT", "all", AS_OF
            ),
        ),
        AS_OF,
    )
    assert len(before_items) == 1
    assert any(finding.code.startswith("missing_responsible") for finding in before.findings)

    ownership_context.actions.confirm_source_owner(
        ownership_context.bid.bid_id, subject_id, "jason"
    )

    after_items = dashboard.filtered_list(
        ownership_context.bid.bid_id, "manufacturer-inputs", "unassigned", AS_OF
    )
    after = evaluate_submission_readiness(
        ReadinessFacts(
            bid_id=ownership_context.bid.bid_id,
            manufacturer_inputs=ownership_context.projection.items(
                ownership_context.bid.bid_id, "MANUFACTURER_INPUT", "all", AS_OF
            ),
        ),
        AS_OF,
    )
    tile = next(
        item
        for item in dashboard.dashboard(ownership_context.bid.bid_id, AS_OF).tiles
        if item.key == "manufacturer_inputs_unassigned"
    )
    assert after_items == [] and tile.count == len(after_items)
    assert not any(finding.code.startswith("missing_responsible") for finding in after.findings)


def test_confirm_refuses_overwrite_and_bulk_is_all_or_nothing(
    ownership_context: ActionContext,
) -> None:
    first = _manufacturer(ownership_context, "First drawing")
    second = _manufacturer(ownership_context, "Second drawing")
    ownership_context.actions.update_manufacturer_input(
        ownership_context.bid.bid_id, second, {"responsible": "Execution lead"}, "jason"
    )
    before = _audit_count(ownership_context)

    with pytest.raises(ValueError, match="already has an explicit execution owner"):
        ownership_context.actions.confirm_source_owner(
            ownership_context.bid.bid_id, second, "jason"
        )
    with pytest.raises(ValueError, match="already has an explicit execution owner"):
        ownership_context.actions.bulk_confirm_source_owners(
            ownership_context.bid.bid_id, [first, second], "jason"
        )
    with pytest.raises(ValueError, match="select at least one"):
        ownership_context.actions.bulk_confirm_source_owners(
            ownership_context.bid.bid_id, [], "jason"
        )

    assert (
        ownership_context.execution_service.get_record(
            ownership_context.bid.bid_id, "MANUFACTURER_INPUT", first
        )
        is None
    )
    assert _audit_count(ownership_context) == before


def test_confirm_requires_a_source_owner_and_bulk_repeat_is_idempotent(
    ownership_context: ActionContext,
) -> None:
    first = _manufacturer(ownership_context, "Bulk one")
    second = _manufacturer(ownership_context, "Bulk two")
    no_owner = _manufacturer(ownership_context, "No owner")
    current = ownership_context.vendors.get_requirement(no_owner)
    assert current is not None
    ownership_context.vendor_service.update_verification(
        no_owner, {"expected_version": current.version, "internal_owner": None}, "jason"
    )
    with pytest.raises(ValueError, match="no source owner"):
        ownership_context.actions.confirm_source_owner(
            ownership_context.bid.bid_id, no_owner, "jason"
        )

    changed = ownership_context.actions.bulk_confirm_source_owners(
        ownership_context.bid.bid_id, [first, second], "jason"
    )
    before_repeat = _audit_count(ownership_context)
    versions = [
        ownership_context.execution_service.get_record(
            ownership_context.bid.bid_id, "MANUFACTURER_INPUT", subject_id
        ).version
        for subject_id in (first, second)
    ]
    repeated = ownership_context.actions.bulk_confirm_source_owners(
        ownership_context.bid.bid_id, [first, second], "jason"
    )
    assert changed.changed and not repeated.changed
    assert _audit_count(ownership_context) == before_repeat
    assert versions == [
        ownership_context.execution_service.get_record(
            ownership_context.bid.bid_id, "MANUFACTURER_INPUT", subject_id
        ).version
        for subject_id in (first, second)
    ]


def test_legacy_owner_migrates_as_assigned_and_persists_after_reinstantiation(
    tmp_path: Path, valid_bid: Bid
) -> None:
    db = Database(tmp_path / "legacy-stage3.db")
    bids = _context(db, valid_bid, create_bid=True).bids
    timestamp = datetime(2026, 7, 10, 14, 30, tzinfo=UTC).isoformat()
    with db._conn() as conn:
        conn.execute("DROP TABLE bid_execution_records")
        conn.execute(
            """CREATE TABLE bid_execution_records(
            execution_id TEXT PRIMARY KEY,bid_id TEXT NOT NULL,subject_kind TEXT NOT NULL,
            subject_id TEXT NOT NULL,responsible TEXT,contributors_json TEXT NOT NULL,
            internal_due_date TEXT,prep_status TEXT,priority TEXT,blocked_reason TEXT,
            completion_evidence TEXT,evidence_document_version_id TEXT,version INTEGER NOT NULL,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL,updated_by TEXT NOT NULL,
            UNIQUE(subject_kind,subject_id))"""
        )
        conn.execute(
            "INSERT INTO bid_execution_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy-record",
                valid_bid.bid_id,
                "MANUFACTURER_INPUT",
                "legacy-subject",
                "Legacy owner",
                "[]",
                None,
                None,
                None,
                None,
                None,
                None,
                3,
                timestamp,
                timestamp,
                "legacy actor",
            ),
        )

    repository = BidExecutionRepository(db)
    record = repository.get_record(ExecutionSubjectKind.MANUFACTURER_INPUT, "legacy-subject")
    with db._conn() as conn:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(bid_execution_records)").fetchall()
        }
    assert record is not None
    assert record.responsible_basis is ResponsibleBasis.ASSIGNED
    assert record.version == 3 and record.responsible == "Legacy owner"
    assert {"responsible_basis", "responsible_source_owner", "responsible_set_by"} <= columns
    assert (
        BidExecutionRepository(db).get_record(
            ExecutionSubjectKind.MANUFACTURER_INPUT, "legacy-subject"
        )
        == record
    )
    assert bids.get_bid(valid_bid.bid_id) is not None


def test_confirmation_http_paths_and_visible_provenance(
    ownership_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = ownership_app
    bid_id = ids["bid"]
    subject_id = ids["manufacturer"]
    list_path = f"/bids/{bid_id}/execution/manufacturer-inputs"
    item_path = f"/bids/{bid_id}/execution/items/manufacturer-input/{subject_id}"

    initial = _get(module, item_path)[1]
    assert "Unassigned" in initial
    assert "Source owner: Vendor lead — not confirmed as execution owner" in initial
    assert "Confirm Vendor lead as owner" in initial

    status, _headers, _body = _post(
        module,
        f"{list_path}/items/{subject_id}/confirm-source-owner",
        {"return_to": item_path},
    )
    assert status == 303
    confirmed = _get(module, item_path)[1]
    record = module.bid_execution_service.get_record(bid_id, "MANUFACTURER_INPUT", subject_id)
    assert record is not None and record.responsible_set_at is not None
    assert (
        "Confirmed from the source owner by "
        f"{record.responsible_set_by} on {record.responsible_set_at:%Y-%m-%d}"
    ) in confirmed
    visible = re.sub(r"<[^>]+>", " ", confirmed)
    assert subject_id not in visible

    package_id = module.vendor_document_repository.get_requirement(subject_id).package_id
    bulk_ids = []
    for number in (1, 2):
        requirement = module.vendor_document_service.create_requirement(
            CustomerRequirementCreate(
                package_id=package_id,
                customer_requirement_code=f"BULK-{number}",
                deliverable_title=f"Bulk manufacturer input {number}",
                description="Confirm the recorded source owner.",
            ),
            "Jason",
        )
        bulk_ids.append(requirement.requirement_id)
    status, _headers, _body = _post_pairs(
        module,
        f"{list_path}/confirm-source-owners",
        [
            ("source_subject_ids", bulk_ids[0]),
            ("source_subject_ids", bulk_ids[1]),
            ("return_to", list_path),
        ],
    )
    assert status == 303
    assert all(
        module.bid_execution_service.get_record(
            bid_id, "MANUFACTURER_INPUT", bulk_id
        ).responsible_basis
        is ResponsibleBasis.CONFIRMED_SOURCE_OWNER
        for bulk_id in bulk_ids
    )

    status, _headers, body = _post_pairs(
        module,
        f"{list_path}/confirm-source-owners",
        [("return_to", list_path)],
    )
    assert status == 422
    assert "select at least one manufacturer input" in body
