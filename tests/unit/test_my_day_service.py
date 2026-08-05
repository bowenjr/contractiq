from datetime import UTC, date, datetime
from uuid import UUID

from core.bid_repository import BidRepository
from core.database import Database
from core.my_day import MyDayProjection
from core.readiness import ReadinessReport, ReadinessVerdict
from core.schemas import Bid
from core.work_item_repository import WorkItemRepository
from core.work_item_service import MyDayService, WorkItemService

NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)


def _report(bid_id: str) -> ReadinessReport:
    return ReadinessReport(
        bid_id=bid_id,
        verdict=ReadinessVerdict.HOLD,
        generated_at=NOW,
        summary="Bid is on HOLD: 1 material blocker requires resolution or override.",
        advisory="Fixed test readiness result.",
    )


def test_service_uses_caller_supplied_date_and_task06_readiness_seam(
    tmp_db: Database,
    bid_repo: BidRepository,
    valid_bid: Bid,
) -> None:
    bid_repo.create_bid(valid_bid)
    repository = WorkItemRepository(tmp_db)
    ids = iter((UUID(int=1), UUID(int=2)))
    mutations = WorkItemService(
        repository,
        bid_repo,
        now_factory=lambda: NOW,
        id_factory=lambda: next(ids),
    )
    mutations.create_work_item(
        {
            "bid_id": valid_bid.bid_id,
            "title": "Fixed-date work",
            "due_date": "2026-08-05",
        },
        "jason",
    )
    loaded_bid_ids: list[str] = []

    def load_readiness(bid_id: str) -> ReadinessReport:
        loaded_bid_ids.append(bid_id)
        return _report(bid_id)

    service = MyDayService(
        repository,
        bid_repo,
        tmp_db,
        readiness_loader=load_readiness,
    )

    result: MyDayProjection = service.get_my_day(as_of=date(2026, 8, 5))

    assert result.as_of == date(2026, 8, 5)
    assert [entry.item.title for entry in result.due_today] == ["Fixed-date work"]
    assert result.readiness_holds[0].report == _report(valid_bid.bid_id)
    assert loaded_bid_ids == [valid_bid.bid_id]
