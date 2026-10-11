"""Unit D: register population is idempotent, recoverable and never half-reported as accepted."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import core.deliverable_discovery_repository as repository_module
import core.deliverable_discovery_service as service_module
from core.deliverable_discovery import ProposalReviewDecision
from core.deliverable_discovery_service import ProposalDecisionInput
from tests.unit.test_bid_package_governing_ui import BID_ID
from tests.unit.test_bid_package_governing_ui import governing_ui_app as _app_fixture
from tests.unit.test_stage2_destinations import _accept, _queue, _row, _seed


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    yield from _app_fixture.__wrapped__(tmp_path, monkeypatch)


def _count_vendor(app: ModuleType) -> int:
    repository = app.vendor_document_service.repository
    return sum(
        len(repository.list_requirements(p.package_id)) for p in repository.list_packages(BID_ID)
    )


def _evidence_lines(app: ModuleType, title: str) -> int:
    with app.ops08_repository._conn() as conn:  # type: ignore[attr-defined]
        rows = conn.execute(
            "SELECT supporting_evidence FROM commercial_position_versions"
        ).fetchall()
    return sum(str(r[0] or "").count(title) for r in rows)


def _count_work(app: ModuleType) -> int:
    return len(app.work_item_service.repository.list(BID_ID))


def _reviewed_audits(app: ModuleType, proposal_id: str) -> int:
    with app.deliverable_repository._conn() as conn:  # type: ignore[attr-defined]
        return int(
            conn.execute(
                "SELECT count(*) FROM audit_log WHERE action=? AND detail LIKE ?",
                ("deliverable_discovery_proposal_reviewed", f"{proposal_id}:%"),
            ).fetchone()[0]
        )


# title, register counter
CASES: list[tuple[str, Callable[[ModuleType], int]]] = [
    ("Transformer technical data", _count_vendor),
    ("RFP receipt confirmation", _count_work),
    ("Insurance exceptions", lambda app: _evidence_lines(app, "Insurance exceptions")),
]


@pytest.mark.parametrize(("title", "counter"), CASES, ids=["vendor", "work-item", "commercial"])
def test_interrupted_acceptance_is_visible_as_not_accepted_and_retry_creates_one_record(
    app: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    title: str,
    counter: Callable[[ModuleType], int],
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        real = service_module.accept_into_record

        def die(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("process died after the register record was written")

        monkeypatch.setattr(service_module, "accept_into_record", die)
        first = _accept(app, ids[title])
        # Not reported as accepted: an error comes back and the finding is still pending.
        assert "error" in first and "destination" not in first
        assert _row(app, title)["current_decision"] == "PENDING"
        assert _row(app, title)["destination"] is None
        assert _reviewed_audits(app, ids[title]) == 0
        orphan = counter(app)
        assert orphan == 1  # the record exists, carrying the origin marker (documented behaviour)
        # Two more failed retries do not multiply it.
        for _ in range(2):
            assert "error" in _accept(app, ids[title])
        assert counter(app) == 1
        monkeypatch.setattr(service_module, "accept_into_record", real)
        done = _accept(app, ids[title])
        assert "error" not in done
        assert counter(app) == 1  # recovered by the marker, not re-created
        row = _row(app, title)
        assert row["current_decision"] == "ACCEPTED" and row["destination"] is not None
        assert _reviewed_audits(app, ids[title]) == 1
        # A repeated click after success is refused and creates nothing.
        again = _accept(app, ids[title])
        assert "error" in again and counter(app) == 1 and _reviewed_audits(app, ids[title]) == 1

    asyncio.run(scenario())


def test_deliverable_acceptance_is_all_or_nothing(
    app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        real = repository_module.record_destination

        def die(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("failure at the very last step of the transaction")

        monkeypatch.setattr(repository_module, "record_destination", die)
        result = _accept(app, ids["Form of Bid"])
        assert "error" in result
        assert app.deliverable_repository.list(BID_ID) == []
        assert _row(app, "Form of Bid")["current_decision"] == "PENDING"
        assert _reviewed_audits(app, ids["Form of Bid"]) == 0
        with app.deliverable_repository._conn() as conn:  # type: ignore[attr-defined]
            assert conn.execute("SELECT count(*) FROM deliverable_links").fetchone()[0] == 0
            audits = conn.execute(
                "SELECT count(*) FROM audit_log "
                "WHERE action IN ('deliverable_created','deliverable_activated')"
            ).fetchone()[0]
        assert audits == 0
        monkeypatch.setattr(repository_module, "record_destination", real)
        assert "error" not in _accept(app, ids["Form of Bid"])
        assert len(app.deliverable_repository.list(BID_ID)) == 1

    asyncio.run(scenario())


def test_bulk_accept_with_one_interrupted_finding_loses_and_misreports_nothing(
    app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        real = service_module.accept_into_record
        target = ids["RFP receipt confirmation"]

        def flaky(conn_factory: Any, audit: Any, event: Any, *args: Any, **kwargs: Any) -> None:
            if event.proposal_id == target:
                raise RuntimeError("interrupted")
            real(conn_factory, audit, event, *args, **kwargs)

        monkeypatch.setattr(service_module, "accept_into_record", flaky)
        chosen = ["Form of Bid", "Transformer technical data", "RFP receipt confirmation"]
        results = app.deliverable_discovery_service.decide(
            BID_ID,
            [ProposalDecisionInput(ids[t], ProposalReviewDecision.ACCEPTED) for t in chosen],
            "Jason",
        )
        by_id = {r["proposal_id"]: r for r in results}
        assert "error" not in by_id[ids["Form of Bid"]]
        assert "error" not in by_id[ids["Transformer technical data"]]
        assert "interrupted" in by_id[target]["error"]
        decisions = {r["title"]: r["current_decision"] for r in _queue(app)}
        assert decisions["Form of Bid"] == decisions["Transformer technical data"] == "ACCEPTED"
        assert decisions["RFP receipt confirmation"] == "PENDING"  # never shown as accepted

    asyncio.run(scenario())


def test_rejecting_after_an_interrupted_acceptance_leaves_a_marked_orphan_documented(
    app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DOCUMENTED LIMITATION: the orphan stays in the register, carrying its origin marker."""

    async def scenario() -> None:
        ids = await _seed(app)

        def die(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("interrupted")

        monkeypatch.setattr(service_module, "accept_into_record", die)
        assert "error" in _accept(app, ids["RFP receipt confirmation"])
        [item] = app.work_item_service.repository.list(BID_ID)
        assert f"[DISC:{ids['RFP receipt confirmation']}]" in (item.details or "")
        [result] = app.deliverable_discovery_service.decide(
            BID_ID,
            [
                ProposalDecisionInput(
                    ids["RFP receipt confirmation"],
                    ProposalReviewDecision.REJECTED,
                    rationale="Not needed",
                )
            ],
            "Jason",
        )
        assert "error" not in result
        assert _count_work(app) == 1  # reconciliation of such records is a backlog item

    asyncio.run(scenario())
