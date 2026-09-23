"""Tests for append-only conditional trade progression (TASK-17)."""

from datetime import UTC, datetime

import pytest

from core.database import Database
from core.negotiation import ConditionalTrade, TradeState, ValueState
from core.negotiation_repository import NegotiationRepository
from core.negotiation_service import NegotiationService


def make_service(tmp_db: Database) -> NegotiationService:
    return NegotiationService(NegotiationRepository(tmp_db))


def make_trade(
    service: NegotiationService, value_state: ValueState = ValueState.CLAIMED
) -> ConditionalTrade:
    return service.add_trade(
        ConditionalTrade(
            bid_id="B-1",
            plan_version_id="V-1",
            give="payment extension",
            get="price protection",
            required_value="signed confirmation",
            value_state=value_state,
            state=TradeState.PLANNED,
            created_at=datetime.now(UTC),
        ),
        "operator",
    )


def test_progress_trade_forward_path(tmp_db: Database) -> None:
    service = make_service(tmp_db)
    trade = make_trade(service)
    for state in (TradeState.AUTHORIZED, TradeState.OFFERED, TradeState.TENTATIVELY_AGREED):
        service.progress_trade(trade.trade_id, state, "operator", "advance")
        assert service.current_trade(trade.trade_id).state == state
    # The lineage id is stable across progressions.
    assert service.current_trade(trade.trade_id).trade_lineage_id == trade.trade_id


def test_progress_trade_commit_requires_evidenced_value(tmp_db: Database) -> None:
    service = make_service(tmp_db)
    # A CLAIMED trade cannot reach COMMITTED even from the correct prior state.
    claimed = make_trade(service, value_state=ValueState.CLAIMED)
    for state in (TradeState.AUTHORIZED, TradeState.OFFERED, TradeState.TENTATIVELY_AGREED):
        service.progress_trade(claimed.trade_id, state, "operator", "advance")
    with pytest.raises(ValueError, match="evidenced"):
        service.progress_trade(claimed.trade_id, TradeState.COMMITTED, "operator", "commit")
    # An EVIDENCED trade can commit.
    evidenced = make_trade(service, value_state=ValueState.EVIDENCED)
    for state in (TradeState.AUTHORIZED, TradeState.OFFERED, TradeState.TENTATIVELY_AGREED):
        service.progress_trade(evidenced.trade_id, state, "operator", "advance")
    service.progress_trade(evidenced.trade_id, TradeState.COMMITTED, "operator", "commit")
    assert service.current_trade(evidenced.trade_id).state == TradeState.COMMITTED


def test_progress_trade_rejects_backward_move(tmp_db: Database) -> None:
    service = make_service(tmp_db)
    trade = make_trade(service)
    service.progress_trade(trade.trade_id, TradeState.AUTHORIZED, "operator", "advance")
    with pytest.raises(ValueError, match="cannot move from AUTHORIZED to PLANNED"):
        service.progress_trade(trade.trade_id, TradeState.PLANNED, "operator", "rewind")


def test_progress_trade_rejects_move_from_terminal(tmp_db: Database) -> None:
    service = make_service(tmp_db)
    trade = make_trade(service)
    service.progress_trade(trade.trade_id, TradeState.WITHDRAWN, "operator", "withdraw")
    with pytest.raises(ValueError, match="terminal state"):
        service.progress_trade(trade.trade_id, TradeState.AUTHORIZED, "operator", "revive")


def test_progress_trade_is_append_only(tmp_db: Database) -> None:
    service = make_service(tmp_db)
    trade = make_trade(service)
    original_id = trade.trade_id
    service.progress_trade(original_id, TradeState.AUTHORIZED, "operator", "authorized")

    rows = (
        tmp_db._conn()
        .execute(
            "SELECT trade_id, state, state_version FROM negotiation_trades "
            "WHERE trade_lineage_id=?",
            (original_id,),
        )
        .fetchall()
    )
    by_id = {row["trade_id"]: row for row in rows}
    # The original row is untouched.
    assert by_id[original_id]["state"] == "PLANNED"
    assert by_id[original_id]["state_version"] == 1
    # A new row carries the progression.
    assert len(rows) == 2
    progressed = next(r for r in rows if r["trade_id"] != original_id)
    assert progressed["state"] == "AUTHORIZED"
    assert progressed["state_version"] == 2
    # current_trade reflects the latest row.
    assert service.current_trade(original_id).state == TradeState.AUTHORIZED


def test_progress_trade_unknown_trade_raises(tmp_db: Database) -> None:
    service = make_service(tmp_db)
    with pytest.raises(ValueError, match="not found"):
        service.progress_trade("NTR-does-not-exist", TradeState.AUTHORIZED, "operator", "advance")
