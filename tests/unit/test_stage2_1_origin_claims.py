"""Stage 2.1 Unit D-b1: one register record per accepted finding, enforced by the database."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import core.deliverable_discovery_service as service_module
from core.deliverable_discovery_origins import (
    IN_PROGRESS_MESSAGE,
    ORIGINS_TABLE,
    ensure_origin_tables,
    insert_origin,
)
from core.requirement_lifecycle import DestinationKind
from tests.unit.test_bid_package_governing_ui import BID_ID
from tests.unit.test_bid_package_governing_ui import governing_ui_app as _app_fixture
from tests.unit.test_stage2_acceptance_register_integrity import (
    _count_vendor,
    _count_work,
    _evidence_lines,
)
from tests.unit.test_stage2_destinations import _accept, _seed

THREADS = 6


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    yield from _app_fixture.__wrapped__(tmp_path, monkeypatch)


def _deliverables(app: ModuleType) -> int:
    return len(app.deliverable_repository.list(BID_ID))


CASES: list[tuple[str, DestinationKind, Callable[[ModuleType], int]]] = [
    ("Transformer technical data", DestinationKind.VENDOR_DOCUMENT, _count_vendor),
    ("RFP receipt confirmation", DestinationKind.WORK_ITEM, _count_work),
    (
        "Insurance exceptions",
        DestinationKind.COMMERCIAL_POSITION,
        lambda app: _evidence_lines(app, "Insurance exceptions"),
    ),
    ("Form of Bid", DestinationKind.DELIVERABLE, _deliverables),
]
IDS = ["vendor", "work-item", "commercial", "deliverable"]


def _origins(app: ModuleType, proposal_id: str, active_only: bool = True) -> list[sqlite3.Row]:
    clause = " AND released_at IS NULL" if active_only else ""
    with app.deliverable_discovery_repository._conn() as conn:
        return conn.execute(
            f"SELECT * FROM {ORIGINS_TABLE} WHERE proposal_id=?{clause}", (proposal_id,)
        ).fetchall()


def _accepted_events(app: ModuleType, proposal_id: str) -> int:
    with app.deliverable_discovery_repository._conn() as conn:
        return int(
            conn.execute(
                "SELECT count(*) FROM deliverable_discovery_review_events "
                "WHERE proposal_id=? AND decision='ACCEPTED'",
                (proposal_id,),
            ).fetchone()[0]
        )


@pytest.mark.parametrize(("title", "kind", "counter"), CASES, ids=IDS)
def test_concurrent_accept_creates_exactly_one_record(
    app: ModuleType, title: str, kind: DestinationKind, counter: Callable[[ModuleType], int]
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        proposal_id = ids[title]
        before = counter(app)
        barrier = threading.Barrier(THREADS)

        def attempt(_: int) -> dict[str, Any]:
            barrier.wait()
            return _accept(app, proposal_id)

        with ThreadPoolExecutor(max_workers=THREADS) as pool:
            results = list(pool.map(attempt, range(THREADS)))
        successes = [r for r in results if "error" not in r]
        assert len(successes) == 1, results
        for result in results:
            if "error" in result:
                assert (
                    IN_PROGRESS_MESSAGE in result["error"] or "already accepted" in result["error"]
                ), result
                assert "locked" not in result["error"].lower()
        assert counter(app) - before == 1
        origins = _origins(app, proposal_id)
        assert len(origins) == 1 and origins[0]["destination_kind"] == kind.value
        assert origins[0]["record_id"] is not None
        assert _accepted_events(app, proposal_id) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(("title", "kind", "counter"), CASES, ids=IDS)
def test_repeated_accept_is_refused_and_creates_nothing(
    app: ModuleType, title: str, kind: DestinationKind, counter: Callable[[ModuleType], int]
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        first = _accept(app, ids[title])
        assert "error" not in first
        after_first = counter(app)
        second = _accept(app, ids[title])
        assert "already accepted" in second["error"]
        assert counter(app) == after_first
        assert len(_origins(app, ids[title])) == 1

    asyncio.run(scenario())


def test_failure_after_the_register_write_keeps_the_origin_and_retry_reuses_it(
    app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        proposal_id = ids["RFP receipt confirmation"]
        real = service_module.accept_into_record

        def die(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("process died after the register record was written")

        monkeypatch.setattr(service_module, "accept_into_record", die)
        assert "died" in _accept(app, proposal_id)["error"]
        [origin] = _origins(app, proposal_id)
        assert origin["record_id"] is not None
        assert _accepted_events(app, proposal_id) == 0
        monkeypatch.setattr(service_module, "accept_into_record", real)
        retry = _accept(app, proposal_id)
        assert "error" not in retry
        assert retry["record_id"] == origin["record_id"]
        assert _count_work(app) == 1
        assert len(_origins(app, proposal_id)) == 1
        assert len(_origins(app, proposal_id, active_only=False)) == 1

    asyncio.run(scenario())


def test_failure_before_the_record_exists_releases_the_claim_for_an_immediate_retry(
    app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        proposal_id = ids["Transformer technical data"]
        real = app.deliverable_discovery_service.destinations.register

        def fail(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("register service unavailable")

        monkeypatch.setattr(app.deliverable_discovery_service.destinations, "register", fail)
        assert "unavailable" in _accept(app, proposal_id)["error"]
        assert _origins(app, proposal_id) == []
        monkeypatch.setattr(app.deliverable_discovery_service.destinations, "register", real)
        assert "error" not in _accept(app, proposal_id)
        assert _count_vendor(app) == 1
        assert len(_origins(app, proposal_id)) == 1

    asyncio.run(scenario())


def test_a_dead_request_claim_blocks_until_the_lease_expires_then_is_adopted(
    app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        proposal_id = ids["RFP receipt confirmation"]
        service = app.deliverable_discovery_service
        real_register = service.destinations.register

        def crash(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("request died")

        # A killed process never runs the in-process release; simulate that.
        monkeypatch.setattr(service, "_release_unwritten_claim", lambda *_a, **_k: None)
        monkeypatch.setattr(service.destinations, "register", crash)
        assert "died" in _accept(app, proposal_id)["error"]
        [stale] = _origins(app, proposal_id)
        assert stale["record_id"] is None
        monkeypatch.setattr(service.destinations, "register", real_register)

        assert IN_PROGRESS_MESSAGE in _accept(app, proposal_id)["error"]
        assert _count_work(app) == 0

        real_claim = service_module.claim_origin
        monkeypatch.setattr(
            service_module,
            "claim_origin",
            lambda *a, **k: real_claim(*a, lease_seconds=0, **k),
        )
        assert "error" not in _accept(app, proposal_id)
        assert _count_work(app) == 1
        active = _origins(app, proposal_id)
        assert len(active) == 1 and active[0]["origin_id"] != stale["origin_id"]
        assert len(_origins(app, proposal_id, active_only=False)) == 2

    asyncio.run(scenario())


def test_origin_rows_cannot_be_deleted_or_rewritten(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        assert "error" not in _accept(app, ids["RFP receipt confirmation"])
        [origin] = _origins(app, ids["RFP receipt confirmation"])
        with app.deliverable_discovery_repository._conn() as conn:
            with pytest.raises(sqlite3.DatabaseError, match="cannot be deleted"):
                conn.execute(
                    f"DELETE FROM {ORIGINS_TABLE} WHERE origin_id=?", (origin["origin_id"],)
                )
            with pytest.raises(sqlite3.DatabaseError, match="only change"):
                conn.execute(
                    f"UPDATE {ORIGINS_TABLE} SET record_id='OTHER' WHERE origin_id=?",
                    (origin["origin_id"],),
                )
            with pytest.raises(sqlite3.DatabaseError, match="only change"):
                conn.execute(
                    f"UPDATE {ORIGINS_TABLE} SET claimed_by='x' WHERE origin_id=?",
                    (origin["origin_id"],),
                )

    asyncio.run(scenario())


def test_a_second_active_origin_for_one_finding_or_record_is_rejected_by_the_database(
    app: ModuleType,
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        proposal_id = ids["RFP receipt confirmation"]
        assert "error" not in _accept(app, proposal_id)
        audit = app.deliverable_discovery_repository._audit
        with app.deliverable_discovery_repository._conn() as conn:
            with pytest.raises(ValueError, match="already has an origin"):
                insert_origin(
                    conn,
                    audit,
                    proposal_id=proposal_id,
                    bid_id=BID_ID,
                    kind=DestinationKind.WORK_ITEM,
                    actor="test",
                )
            conn.rollback()
            [origin] = _origins(app, proposal_id)
            other = ids["Transformer technical data"]
            with pytest.raises(ValueError, match="already has an origin"):
                insert_origin(
                    conn,
                    audit,
                    proposal_id=other,
                    bid_id=BID_ID,
                    kind=DestinationKind.WORK_ITEM,
                    actor="test",
                    record_id=origin["record_id"],
                )

    asyncio.run(scenario())


def test_origin_migration_is_repeatable_on_an_existing_database(app: ModuleType) -> None:
    with app.deliverable_discovery_repository._conn() as conn:
        ensure_origin_tables(conn)
        ensure_origin_tables(conn)
        conn.commit()
        assert conn.execute(f"SELECT count(*) FROM {ORIGINS_TABLE}").fetchone()[0] == 0
