"""Stage 2.1 Unit D-b2: explicit reconciliation of incomplete discovery acceptances."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import core.deliverable_discovery_service as service_module
from core.deliverable_discovery import ProposalReviewDecision
from core.deliverable_discovery_destinations import DestinationWriter
from core.deliverable_discovery_origins import ORIGINS_TABLE
from core.deliverable_discovery_reconcile import ReconcileState, reconcile_origins
from core.deliverable_discovery_service import ProposalDecisionInput
from core.work_items import WorkCategory, WorkItemCreate, WorkItemStatus, WorkItemTransition
from scripts.reconcile_discovery_origins import main as reconcile_main
from tests.unit.test_bid_package_governing_ui import BID_ID
from tests.unit.test_bid_package_governing_ui import governing_ui_app as _app_fixture
from tests.unit.test_stage2_acceptance_register_integrity import _count_work, _evidence_lines
from tests.unit.test_stage2_destinations import _accept, _seed

WORK = "RFP receipt confirmation"
EVIDENCE = "Insurance exceptions"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    yield from _app_fixture.__wrapped__(tmp_path, monkeypatch)


def _reconcile(app: ModuleType, **kwargs: Any) -> Any:
    repository = app.deliverable_discovery_repository
    return reconcile_origins(repository._conn, repository._audit, BID_ID, **kwargs)


def _events(app: ModuleType) -> list[tuple[Any, ...]]:
    with app.deliverable_discovery_repository._conn() as conn:
        return [
            tuple(row)
            for row in conn.execute(
                "SELECT * FROM deliverable_discovery_review_events ORDER BY event_id"
            ).fetchall()
        ]


def _break_acceptance(app: ModuleType, monkeypatch: pytest.MonkeyPatch, proposal_id: str) -> None:
    def die(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("process died after the register record was written")

    monkeypatch.setattr(service_module, "accept_into_record", die)
    assert "died" in _accept(app, proposal_id)["error"]


def _forget_origins(app: ModuleType) -> None:
    """Simulate a database from before origins existed (test-only schema surgery)."""
    with app.deliverable_discovery_repository._conn() as conn:
        conn.execute("DROP TRIGGER ddorigin_no_delete")
        conn.execute(f"DELETE FROM {ORIGINS_TABLE}")
        conn.commit()


def _active_origins(app: ModuleType) -> int:
    with app.deliverable_discovery_repository._conn() as conn:
        return int(
            conn.execute(
                f"SELECT count(*) FROM {ORIGINS_TABLE} WHERE released_at IS NULL"
            ).fetchone()[0]
        )


def test_record_without_decision_is_reported_never_decided_and_retry_completes(
    app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        real = service_module.accept_into_record
        _break_acceptance(app, monkeypatch, ids[WORK])
        before = _events(app)

        dry = _reconcile(app)
        assert [i.state for i in dry.items] == [ReconcileState.RECORD_WITHOUT_DECISION]
        applied = _reconcile(app, apply=True)
        assert [i.state for i in applied.items] == [ReconcileState.RECORD_WITHOUT_DECISION]
        assert applied.repaired == 0
        assert _events(app) == before  # no reviewer decision written or changed
        assert _count_work(app) == 1

        monkeypatch.setattr(service_module, "accept_into_record", real)
        assert "error" not in _accept(app, ids[WORK])
        assert _count_work(app) == 1
        assert _reconcile(app).items == []

    asyncio.run(scenario())


def test_orphan_after_a_later_reject_is_reported_and_the_record_is_kept(
    app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        _break_acceptance(app, monkeypatch, ids[WORK])
        [rejected] = app.deliverable_discovery_service.decide(
            BID_ID,
            [
                ProposalDecisionInput(
                    ids[WORK], ProposalReviewDecision.REJECTED, rationale="Not ours"
                )
            ],
            "Jason",
        )
        assert "error" not in rejected
        before = _events(app)
        report = _reconcile(app, apply=True)
        assert [i.state for i in report.items] == [ReconcileState.ORPHAN_AFTER_REJECT]
        assert report.repaired == 0
        assert _count_work(app) == 1  # no unintended deletion
        assert _events(app) == before

    asyncio.run(scenario())


def test_claim_without_record_is_released_only_after_the_lease(
    app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        service = app.deliverable_discovery_service

        def crash(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("request died")

        monkeypatch.setattr(service, "_release_unwritten_claim", lambda *_a, **_k: None)
        monkeypatch.setattr(service.destinations, "register", crash)
        assert "died" in _accept(app, ids[WORK])["error"]
        before = _events(app)

        soon = _reconcile(app, apply=True)
        assert [i.state for i in soon.items] == [ReconcileState.CLAIM_WITHOUT_RECORD]
        assert soon.repaired == 0 and _active_origins(app) == 1

        later = _reconcile(app, apply=True, now=datetime.now(UTC) + timedelta(seconds=300))
        assert later.repaired == 1 and _active_origins(app) == 0
        assert _events(app) == before
        assert _reconcile(app).items == []

    asyncio.run(scenario())


def test_legacy_records_without_origins_are_backfilled_including_closed_work_items(
    app: ModuleType,
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        for title in (WORK, "Transformer technical data", "Form of Bid"):
            assert "error" not in _accept(app, ids[title])
        item = app.work_item_service.repository.list(BID_ID, active_only=False)[0]
        app.work_item_service.transition_work_item(
            item.work_item_id,
            WorkItemTransition(
                expected_version=item.version,
                status=WorkItemStatus.CANCELLED,
                cancellation_reason="Handled elsewhere",
            ),
            "Jason",
        )
        assert app.work_item_service.repository.list(BID_ID, active_only=True) == []  # closed
        _forget_origins(app)
        before = _events(app)

        dry = _reconcile(app)
        states = {i.state for i in dry.items}
        assert states == {ReconcileState.MARKER_WITHOUT_ORIGIN}
        assert len(dry.items) == 3 and _active_origins(app) == 0
        applied = _reconcile(app, apply=True)
        assert applied.repaired == 3 and _active_origins(app) == 3
        assert _events(app) == before
        assert _reconcile(app).items == []

    asyncio.run(scenario())


def test_commercial_history_is_searched_for_origins_and_for_safe_retry(
    app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        real = service_module.accept_into_record
        _break_acceptance(app, monkeypatch, ids[EVIDENCE])
        # A later revision rewrites the evidence text, so the current revision has no marker.
        ops08 = app.ops08_repository
        with ops08._conn() as conn:
            position_id = str(
                conn.execute("SELECT position_id FROM commercial_positions").fetchone()[0]
            )
        detail = ops08.detail(position_id)
        revision = DestinationWriter._current_revision(detail).model_copy(
            update={"supporting_evidence": "Reviewer rewrote the evidence."}
        )
        ops08.revise(position_id, int(detail["current_version"]), revision, "Jason")
        assert "[DISC:" not in str(ops08.detail(position_id)["supporting_evidence"])

        monkeypatch.setattr(service_module, "accept_into_record", real)
        assert "error" not in _accept(app, ids[EVIDENCE])
        assert _evidence_lines(app, EVIDENCE) == 1  # the history marker prevented a second line

        _forget_origins(app)
        report = _reconcile(app, apply=True)
        assert [i.state for i in report.items] == [ReconcileState.MARKER_WITHOUT_ORIGIN]
        assert report.repaired == 1
        with app.deliverable_discovery_repository._conn() as conn:
            row = conn.execute(f"SELECT * FROM {ORIGINS_TABLE}").fetchone()
        assert row["record_id"] == position_id and "position version" in row["record_detail"]

    asyncio.run(scenario())


def test_duplicate_markers_are_reported_and_nothing_is_changed(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        assert "error" not in _accept(app, ids[WORK])
        marker = f"[DISC:{ids[WORK]}]"
        app.work_item_service.create_work_item(
            WorkItemCreate(
                bid_id=BID_ID,
                title="Second copy",
                details=f"duplicate {marker}",
                category=WorkCategory.CUSTOMER_REQUEST,
            ),
            "Jason",
        )
        before = _events(app)
        report = _reconcile(app, apply=True)
        assert [i.state for i in report.items] == [ReconcileState.DUPLICATE_MARKERS]
        assert report.repaired == 0 and _events(app) == before
        assert _count_work(app) == 2

    asyncio.run(scenario())


def test_script_is_a_dry_run_by_default_and_never_targets_a_missing_database(
    app: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        _break_acceptance(app, monkeypatch, ids[WORK])
        db_path = app.deliverable_discovery_repository.db.db_path
        assert reconcile_main(["--db", db_path, "--bid", BID_ID]) == 1
        assert "RECORD_WITHOUT_DECISION" in capsys.readouterr().out
        assert reconcile_main(["--db", db_path, "--bid", BID_ID, "--apply"]) == 0
        assert reconcile_main(["--db", db_path + ".missing"]) == 2

    asyncio.run(scenario())
