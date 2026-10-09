"""Append-only customer-release-reference confirmation (UNCONFIRMED / PROVIDED / NONE)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from urllib.parse import urlencode

import pytest

from scripts.asgi_acceptance_ops08 import request
from tests.unit.test_bid_package_governing_ui import BID_ID, _register_release
from tests.unit.test_bid_package_governing_ui import governing_ui_app as _governing_ui_app

ROOT = f"/bids/{BID_ID}/package-intake-addenda"


@pytest.fixture
def ref_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    yield from _governing_ui_app.__wrapped__(tmp_path, monkeypatch)


def _post(app: ModuleType, release_id: str, form: dict[str, str]) -> tuple[int, bytes]:
    status, _, body = asyncio.run(
        request(
            app.app,
            "POST",
            f"{ROOT}/releases/{release_id}/reference-confirmation",
            body=urlencode(form).encode(),
        )
    )
    return status, body


def _count(app: ModuleType, table: str) -> int:
    with sqlite3.connect(app.db.db_path) as conn:
        return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def _release_id(path: str) -> str:
    return path.rsplit("/", 1)[1]


def _advisory_codes(app: ModuleType) -> list[str]:
    return [a.code for a in app.bid_package_service.summary(BID_ID).attention]


def _register(app: ModuleType, **kwargs: str) -> str:
    return _release_id(asyncio.run(_register_release(app, **kwargs)))


def test_empty_or_unchosen_input_is_never_read_as_none_provided(ref_app: ModuleType) -> None:
    release_id = _register(ref_app, reference="")
    assert ref_app.bid_package_repository.reference_status(release_id)["reference_state"] == (
        "UNCONFIRMED"
    )
    assert "RELEASE_REFERENCE_MISSING" in _advisory_codes(ref_app)
    events, audits = (
        _count(ref_app, "bid_release_reference_confirmations"),
        _count(ref_app, "audit_log"),
    )
    cases = [
        {"operation_id": "o1"},  # nothing chosen
        {"operation_id": "o2", "state": "REFERENCE_PROVIDED", "exact_customer_reference": "  "},
        {
            "operation_id": "o3",
            "state": "NONE_PROVIDED_BY_CUSTOMER",
            "exact_customer_reference": "RFQ 1",
        },
        {"operation_id": "o4", "state": "UNCONFIRMED"},
    ]
    for form in cases:
        status, _ = _post(ref_app, release_id, form)
        assert status == 422, form
    assert _count(ref_app, "bid_release_reference_confirmations") == events
    assert _count(ref_app, "audit_log") == audits
    assert "RELEASE_REFERENCE_MISSING" in _advisory_codes(ref_app)


def test_provided_then_corrected_to_none_keeps_history_and_clears_the_advisory(
    ref_app: ModuleType,
) -> None:
    release_id = _register(ref_app, reference="")
    page = asyncio.run(request(ref_app.app, "GET", f"{ROOT}/releases/{release_id}"))[2].decode()
    assert 'id="reference"' in page and "Not yet confirmed" in page
    assert "The customer supplied a reference" in page

    status, _ = _post(
        ref_app,
        release_id,
        {
            "operation_id": "op-1",
            "state": "REFERENCE_PROVIDED",
            "exact_customer_reference": "RFQ 2026-114 — Issued for Bid",
        },
    )
    assert status == 303
    state = ref_app.bid_package_repository.reference_status(release_id)
    assert state["reference_state"] == "REFERENCE_PROVIDED"
    assert state["effective_reference"] == "RFQ 2026-114 — Issued for Bid"
    assert state["reference_source"] == "CONFIRMATION"
    assert state["confirmed_by"] and state["confirmed_at"]
    assert "RELEASE_REFERENCE_MISSING" not in _advisory_codes(ref_app)
    # The imported evidence row is untouched; only the effective view changed.
    assert (
        ref_app.bid_package_repository.release(release_id, BID_ID)["imported_customer_reference"]
        is None
    )
    page = asyncio.run(request(ref_app.app, "GET", ROOT))[2].decode()
    assert "RFQ 2026-114 — Issued for Bid" in page

    first = ref_app.bid_package_repository.reference_history(release_id)[0]
    # Replaying the same operation is idempotent; omitting the supersession is stale.
    assert (
        _post(
            ref_app,
            release_id,
            {
                "operation_id": "op-1",
                "state": "REFERENCE_PROVIDED",
                "exact_customer_reference": "RFQ 2026-114 — Issued for Bid",
            },
        )[0]
        == 303
    )
    assert _count(ref_app, "bid_release_reference_confirmations") == 1
    status, body = _post(
        ref_app, release_id, {"operation_id": "op-2", "state": "NONE_PROVIDED_BY_CUSTOMER"}
    )
    assert status == 422 and b"explicitly superseded" in body
    status, _ = _post(
        ref_app,
        release_id,
        {
            "operation_id": "op-3",
            "state": "NONE_PROVIDED_BY_CUSTOMER",
            "supersedes_event_id": first["confirmation_event_id"],
            "note": "Checked the cover letter and portal",
        },
    )
    assert status == 303
    state = ref_app.bid_package_repository.reference_status(release_id)
    assert state["reference_state"] == "NONE_PROVIDED_BY_CUSTOMER"
    assert state["effective_reference"] is None
    assert "RELEASE_REFERENCE_MISSING" not in _advisory_codes(ref_app)
    # A concurrent second correction of the already-superseded event is refused.
    status, body = _post(
        ref_app,
        release_id,
        {
            "operation_id": "op-4",
            "state": "REFERENCE_PROVIDED",
            "exact_customer_reference": "X",
            "supersedes_event_id": first["confirmation_event_id"],
        },
    )
    assert status == 422 and b"superseded" in body

    history = ref_app.bid_package_repository.reference_history(release_id)
    assert [h["state"] for h in history] == ["REFERENCE_PROVIDED", "NONE_PROVIDED_BY_CUSTOMER"]
    assert history[1]["supersedes_event_id"] == first["confirmation_event_id"]
    assert history[0] == first  # earlier event unchanged
    with sqlite3.connect(ref_app.db.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE bid_release_reference_confirmations SET note='x'")
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute("DELETE FROM bid_release_reference_confirmations")
        conn.execute("PRAGMA foreign_keys=ON")
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        actions = [
            row[0]
            for row in conn.execute(
                "SELECT action FROM audit_log WHERE action='bid_release_reference_confirmed'"
            )
        ]
    assert len(actions) == 2
    page = asyncio.run(request(ref_app.app, "GET", f"{ROOT}/releases/{release_id}"))[2].decode()
    assert "Customer provided none" in page and "Confirmation history (2)" in page


def test_import_time_reference_counts_as_provided_and_can_be_corrected(
    ref_app: ModuleType,
) -> None:
    release_id = _register(ref_app, reference="Synthetic initial package")
    state = ref_app.bid_package_repository.reference_status(release_id)
    assert state["reference_state"] == "REFERENCE_PROVIDED"
    assert state["reference_source"] == "IMPORT"
    assert "RELEASE_REFERENCE_MISSING" not in _advisory_codes(ref_app)
    assert (
        _post(
            ref_app,
            release_id,
            {
                "operation_id": "fix-1",
                "state": "REFERENCE_PROVIDED",
                "exact_customer_reference": "RFQ 2026-114",
            },
        )[0]
        == 303
    )
    assert (
        ref_app.bid_package_repository.reference_status(release_id)["effective_reference"]
        == "RFQ 2026-114"
    )


def test_confirmation_after_publication_is_flagged_without_changing_the_snapshot(
    ref_app: ModuleType,
) -> None:
    release_id = _register(ref_app, reference="")
    repo = ref_app.bid_package_repository
    with sqlite3.connect(ref_app.db.db_path) as conn:
        conn.execute(
            "INSERT INTO bid_basis_snapshots VALUES("
            "'BBS-T',?,1,NULL,'Bid Basis 1',NULL,?,'op','Jason','2026-09-15T00:00:00+00:00','{}')",
            (BID_ID, "a" * 64),
        )
        conn.execute(
            "INSERT INTO bid_basis_snapshot_releases VALUES('BBSR-T','BBS-T',?,1,'Jason',"
            "'2026-09-15T00:00:00+00:00','{}')",
            (release_id,),
        )
    snapshot_before = tuple(
        sqlite3.connect(ref_app.db.db_path)
        .execute("SELECT * FROM bid_basis_snapshots WHERE snapshot_id='BBS-T'")
        .fetchone()
    )
    register = repo.basis_register(BID_ID, "BBS-T")
    assert register["releases"][0]["reference_state"] == "UNCONFIRMED"
    assert register["releases"][0]["reference_confirmed_after_publication"] is False

    assert (
        _post(
            ref_app,
            release_id,
            {
                "operation_id": "late",
                "state": "REFERENCE_PROVIDED",
                "exact_customer_reference": "RFQ 2026-114",
            },
        )[0]
        == 303
    )
    register = repo.basis_register(BID_ID, "BBS-T")
    row = register["releases"][0]
    assert row["exact_customer_reference"] == "RFQ 2026-114"
    assert row["reference_confirmed_after_publication"] is True
    snapshot_after = tuple(
        sqlite3.connect(ref_app.db.db_path)
        .execute("SELECT * FROM bid_basis_snapshots WHERE snapshot_id='BBS-T'")
        .fetchone()
    )
    assert snapshot_after == snapshot_before  # prior snapshot preserved; no new one is forced
    assert _count(ref_app, "bid_basis_snapshots") == 1
    csv_text = ref_app.bid_package_service.basis_register_csv(BID_ID, "BBS-T").decode()
    assert "RFQ 2026-114" in csv_text and "Customer reference" in csv_text
    assert "Not supplied" not in csv_text
    page = asyncio.run(request(ref_app.app, "GET", f"{ROOT}/bid-basis-register"))[2].decode()
    assert "after this Bid Basis was published" in page and "RFQ 2026-114" in page
