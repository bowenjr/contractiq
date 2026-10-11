"""A2: collision evidence prevents title-only register reuse and is visible in review."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from tests.unit.test_bid_package_governing_ui import BID_ID
from tests.unit.test_bid_package_governing_ui import governing_ui_app as _app_fixture
from tests.unit.test_deliverable_discovery_job_routes import _include, _start
from tests.unit.test_deliverable_discovery_service import _pdf_bytes
from tests.unit.test_stage2_destinations import _accept, _queue, _req
from tests.unit.test_stage2_review import _get

DELIVERABLE_TITLE = "<Shared & plan>"
WORK_TITLE = "Site preparation check"
VENDOR_TITLE = "Manufacturer certificates"

D1 = "The Bidder shall submit the electrical safety plan with its Proposal."
D2 = "The Bidder shall submit the environmental spill plan with its Proposal."
W1 = "The Bidder shall inspect the north access road before submitting its bid."
W2 = "The Bidder shall schedule the south gate briefing before submitting its bid."
V1 = "The Bidder shall obtain transformer test certificates from the manufacturer."
V2 = "The Bidder shall obtain pump material certificates from the manufacturer."
PASSAGE = "\n".join((D1, D2, W1, W2, V1, V2))


class _CollisionModel:
    def complete_json(self, *_args: object, **_kwargs: object) -> object:
        return {
            "requirements": [
                _req(
                    DELIVERABLE_TITLE,
                    "BID_RETURNABLE",
                    D1,
                    description="Electrical isolation procedures for energized equipment.",
                ),
                _req(
                    DELIVERABLE_TITLE,
                    "BID_RETURNABLE",
                    D2,
                    description="Environmental spill response methods for fuel releases.",
                ),
                _req(
                    WORK_TITLE,
                    "PRE_BID_ACTION",
                    W1,
                    description="Inspect access conditions on the north road.",
                    item_kind="CLARIFICATION",
                ),
                _req(
                    WORK_TITLE,
                    "PRE_BID_ACTION",
                    W2,
                    description="Arrange the security briefing at the south gate.",
                    item_kind="CLARIFICATION",
                ),
                _req(
                    VENDOR_TITLE,
                    "MANUFACTURER_INPUT",
                    V1,
                    description="Obtain transformer factory test certificates.",
                    item_kind="MANUFACTURER_DATA",
                ),
                _req(
                    VENDOR_TITLE,
                    "MANUFACTURER_INPUT",
                    V2,
                    description="Obtain pump material traceability certificates.",
                    item_kind="MANUFACTURER_DATA",
                ),
            ]
        }


@pytest.fixture  # type: ignore[untyped-decorator]
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """Mirror the real temp-database governing_ui_app fixture used by Stage 2 acceptance."""
    yield from _app_fixture.__wrapped__(tmp_path, monkeypatch)


async def _seed_collisions(app: ModuleType) -> dict[str, list[str]]:
    await _include(app, "CollisionA2", {"collision & evidence.pdf": _pdf_bytes([PASSAGE])})
    app.discovery_job_runner.llm_client = _CollisionModel()
    await _start(app, "op-collision-a2")
    assert app.discovery_job_runner.run_pending() == 1
    by_title: dict[str, list[str]] = {}
    for row in _queue(app):
        by_title.setdefault(str(row["title"]), []).append(str(row["proposal_id"]))
    assert {title: len(ids) for title, ids in by_title.items()} == {
        DELIVERABLE_TITLE: 2,
        WORK_TITLE: 2,
        VENDOR_TITLE: 2,
    }
    return by_title


def test_colliding_deliverable_findings_create_two_deliverables(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = (await _seed_collisions(app))[DELIVERABLE_TITLE]
        results = [_accept(app, proposal_id) for proposal_id in ids]

        assert all("error" not in result for result in results)
        assert len({result["deliverable_id"] for result in results}) == 2
        assert [row["title"] for row in app.deliverable_repository.list(BID_ID)].count(
            DELIVERABLE_TITLE
        ) == 2

    asyncio.run(scenario())


def test_colliding_work_item_findings_create_two_work_items(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = (await _seed_collisions(app))[WORK_TITLE]
        results = [_accept(app, proposal_id) for proposal_id in ids]

        assert all("error" not in result for result in results)
        assert len({result["record_id"] for result in results}) == 2
        assert [item.title for item in app.work_item_service.repository.list(BID_ID)].count(
            WORK_TITLE
        ) == 2

    asyncio.run(scenario())


def test_colliding_vendor_findings_create_two_requirements(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = (await _seed_collisions(app))[VENDOR_TITLE]
        results = [_accept(app, proposal_id) for proposal_id in ids]

        assert all("error" not in result for result in results)
        assert len({result["record_id"] for result in results}) == 2
        repository = app.vendor_document_service.repository
        [package] = repository.list_packages(BID_ID)
        requirements = repository.list_requirements(package.package_id)
        assert [item.deliverable_title for item in requirements].count(VENDOR_TITLE) == 2

    asyncio.run(scenario())


def test_non_colliding_same_title_finding_keeps_title_reuse(app: ModuleType) -> None:
    class _NonCollisionModel:
        def complete_json(self, *_args: object, **_kwargs: object) -> object:
            return {
                "requirements": [
                    _req("Shared submission", "BID_RETURNABLE", D1),
                    _req(
                        "Shared submission",
                        "CONDITIONAL",
                        D2,
                        requirement_status="CONDITIONAL",
                        condition_text="If environmental controls are required",
                        applies_at="WITH_BID",
                    ),
                ]
            }

    async def scenario() -> None:
        await _include(app, "NoCollisionA2", {"same-title.pdf": _pdf_bytes([PASSAGE])})
        app.discovery_job_runner.llm_client = _NonCollisionModel()
        await _start(app, "op-no-collision-a2")
        assert app.discovery_job_runner.run_pending() == 1
        rows = [row for row in _queue(app) if row["title"] == "Shared submission"]
        assert len(rows) == 2 and all(row["collisions"] == [] for row in rows)

        first = _accept(app, rows[0]["proposal_id"])
        second = _accept(app, rows[1]["proposal_id"])
        assert "error" not in first and "error" not in second
        assert second["reused"] is True
        assert second["deliverable_id"] == first["deliverable_id"]
        assert len(app.deliverable_repository.list(BID_ID)) == 1

    asyncio.run(scenario())


def test_review_queue_and_page_show_escaped_collision_note(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = (await _seed_collisions(app))[DELIVERABLE_TITLE]
        row = next(item for item in _queue(app) if item["proposal_id"] == ids[0])

        assert row["collisions"] == [
            {
                "title": DELIVERABLE_TITLE,
                "document": "collision & evidence.pdf",
                "locator": "p.1",
                "reasons": ["description"],
                "current_decision": "PENDING",
            }
        ]
        page = await _get(app, "?lifecycle=bid")
        assert (
            "Possible collision with: &lt;Shared &amp; plan&gt; "
            "(collision &amp; evidence.pdf, p.1) — same title, differs in description"
        ) in page
        assert "Possible collision with: <Shared & plan>" not in page

    asyncio.run(scenario())
