"""Socketless route coverage for the read-only Stage 3 execution screens."""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from urllib.parse import unquote

import pytest

from core.deliverable_discovery import (
    DeliverableProposal,
    DiscoveryCriticality,
    DiscoveryRun,
    ExtractionStatus,
    ExtractionUnit,
    LocatorType,
    RunStatus,
    SourceCitation,
    normalize_dedup_key,
)
from core.deliverables import (
    Deliverable,
    DeliverableCriticality,
    DeliverableDirection,
    DeliverableState,
    LifecyclePhase,
)
from core.schemas import Bid, Provenance
from core.vendor_document_control import CustomerRequirementCreate, SupplierPackageCreate
from scripts.asgi_acceptance_ops08 import request

AS_OF = date(2026, 7, 10)
NOW = datetime(2026, 7, 10, 14, 30, tzinfo=UTC)


@pytest.fixture
def ui_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    valid_bid: Bid,
) -> tuple[ModuleType, dict[str, str]]:
    monkeypatch.setenv("CONTRACTIQ_DB_PATH", str(tmp_path / "stage3-ui.db"))
    monkeypatch.setenv("CONTRACTIQ_DOCUMENT_ROOT", str(tmp_path / "documents"))
    monkeypatch.setenv("CONTRACTIQ_INTAKE_STORAGE_ROOT", str(tmp_path / "intake"))
    monkeypatch.setenv("CONTRACTIQ_PROPOSAL_ARTIFACT_ROOT", str(tmp_path / "proposals"))
    sys.modules.pop("app", None)
    app_path = Path(__file__).parents[2] / "app.py"
    spec = importlib.util.spec_from_file_location("app", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load app.py for Stage 3 UI tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules["app"] = module
    spec.loader.exec_module(module)
    module._working_date = lambda: AS_OF
    module.bid_repository.create_bid(valid_bid)

    run = DiscoveryRun(
        bid_id=valid_bid.bid_id,
        operation_id="stage3-ui-source",
        status=RunStatus.SUCCEEDED,
        proposal_count=1,
        actor="Jason",
        started_at=NOW,
        completed_at=NOW,
    )
    source_quote = "The bidder shall submit the signed compliance schedule with its offer."
    proposal = DeliverableProposal(
        bid_id=valid_bid.bid_id,
        run_id=run.run_id,
        title="Signed compliance schedule",
        category="FORM",
        criticality=DiscoveryCriticality.REQUIRED,
        sources=(
            SourceCitation(
                document_version_id="DV-source-placeholder",
                locator_type=LocatorType.PAGE,
                locator="page 7",
                excerpt=source_quote,
                quote=source_quote,
            ),
        ),
        dedup_key=normalize_dedup_key(valid_bid.bid_id, "Signed compliance schedule", "FORM"),
        created_at=NOW,
    )
    extraction = ExtractionUnit(
        bid_id=valid_bid.bid_id,
        run_id=run.run_id,
        document_version_id="DV-source-placeholder",
        locator_type=LocatorType.PAGE,
        locator="page 7",
        text_excerpt=source_quote,
        char_count=len(source_quote),
        status=ExtractionStatus.OK,
        created_at=NOW,
    )
    module.deliverable_discovery_repository.create_run(run, [extraction], [proposal], "Jason")
    with module.db._conn() as conn:
        conn.execute(
            """INSERT INTO deliverable_discovery_review_events(
            event_id,proposal_id,bid_id,decision,reviewer,rationale,linked_deliverable_id,
            supersedes_event_id,reviewed_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                "review-stage3-ui",
                proposal.proposal_id,
                valid_bid.bid_id,
                "ACCEPTED",
                "Jason",
                None,
                "DEL-STAGE3-UI",
                None,
                NOW.isoformat(),
            ),
        )

    work = module.work_item_service.create_work_item(
        {
            "bid_id": valid_bid.bid_id,
            "title": "Confirm compliance wording",
            "details": f"Check the source [DISC:{proposal.proposal_id}]",
            "priority": "HIGH",
            "due_date": date(2026, 7, 9),
        },
        "Jason",
    )
    module.bid_execution_service.add_note(
        valid_bid.bid_id,
        "WORK_ITEM",
        work.work_item_id,
        "Legal review requested.",
        "Jason",
    )
    deliverable = Deliverable(
        deliverable_id="DEL-STAGE3-UI",
        bid_id=valid_bid.bid_id,
        title="Signed compliance schedule",
        description="Prepare the signed customer form.",
        category="Submission",
        criticality=DeliverableCriticality.MANDATORY,
        lifecycle_phase=LifecyclePhase.WITH_BID,
        direction=DeliverableDirection.INTERNAL,
        workflow_state=DeliverableState.ACTIVE,
        provenance=Provenance.from_human("Jason"),
        created_at=NOW,
        updated_at=NOW,
        created_by="Jason",
    )
    module.deliverable_repository.create(deliverable, "Jason")
    package = module.vendor_document_service.create_package(
        SupplierPackageCreate(
            bid_id=valid_bid.bid_id,
            package_name="Pump package",
            package_code="PKG-PUMP",
            proposed_manufacturer="Atlas",
            internal_owner="Vendor lead",
        ),
        "Jason",
    )
    manufacturer = module.vendor_document_service.create_requirement(
        CustomerRequirementCreate(
            package_id=package.package_id,
            customer_requirement_code="DATA-SHEET",
            deliverable_title="Manufacturer data sheet",
            description="Obtain the completed data sheet.",
        ),
        "Jason",
    )
    return module, {
        "bid": valid_bid.bid_id,
        "work": work.work_item_id,
        "returnable": deliverable.deliverable_id,
        "manufacturer": manufacturer.requirement_id,
        "finding": proposal.proposal_id,
        "quote": source_quote,
    }


def _get(module: ModuleType, path: str) -> tuple[int, str]:
    status, _headers, body = asyncio.run(request(module.app, "GET", path))
    return status, body.decode()


def _without_hrefs(html: str) -> str:
    return re.sub(r'href="[^"]*"', 'href=""', html)


def test_every_execution_get_screen_renders_and_item_source_is_traceable(
    ui_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = ui_app
    bid = ids["bid"]
    paths = [
        f"/bids/{bid}/execution",
        f"/bids/{bid}/execution/returnables",
        f"/bids/{bid}/execution/manufacturer-inputs",
        f"/bids/{bid}/execution/actions",
        f"/bids/{bid}/execution/commercial",
        f"/bids/{bid}/execution/overdue",
        f"/bids/{bid}/execution/items/work-item/{ids['work']}",
        f"/bids/{bid}/execution/items/returnable/{ids['returnable']}",
        f"/bids/{bid}/execution/items/manufacturer-input/{ids['manufacturer']}",
        f"/bids/{bid}/execution/source/{ids['finding']}",
        f"/bids/{bid}/execution/readiness",
    ]
    pages = [_get(module, path) for path in paths]
    assert all(status == 200 for status, _html in pages)
    item_html = pages[6][1]
    source_html = pages[9][1]
    assert ids["quote"] in item_html
    assert "Open the originating requirement" in item_html
    assert ids["quote"] in source_html
    assert "Accepted" in source_html


def test_dashboard_tiles_and_list_tabs_reconcile_to_rendered_rows(
    ui_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = ui_app
    dashboard = _get(module, f"/bids/{ids['bid']}/execution")[1]
    tile_pattern = re.compile(
        r'<a class="execution-tile [^"]*" data-tile="[^"]+" data-count="(\d+)" href="([^"]+)"'
    )
    for count, encoded_url in tile_pattern.findall(dashboard):
        url = unquote(encoded_url.replace("&amp;", "&"))
        if url.endswith("/readiness"):
            continue
        status, page = _get(module, url)
        assert status == 200
        assert int(count) == page.count('class="execution-row"')

    for slug in ("returnables", "manufacturer-inputs", "actions", "commercial", "overdue"):
        page = _get(module, f"/bids/{ids['bid']}/execution/{slug}")[1]
        tab_pattern = re.compile(
            rf'href="(/bids/{ids["bid"]}/execution/{slug}\?state=[^"]+)"[^>]*>'
            r'[^<]+ <span class="execution-tab-count">(\d+)</span>'
        )
        for url, count in tab_pattern.findall(page):
            status, filtered = _get(module, url.replace("&amp;", "&"))
            assert status == 200
            assert int(count) == filtered.count('class="execution-row"')


def test_invalid_and_unknown_execution_urls_never_return_500(
    ui_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = ui_app
    bid = ids["bid"]
    checks = {
        f"/bids/{bid}/execution/actions?state=clarification": 422,
        f"/bids/{bid}/execution/actions?state=not-a-filter": 422,
        f"/bids/{bid}/execution/not-a-list": 404,
        "/bids/B-2099-9999/execution": 404,
        f"/bids/{bid}/execution/items/work-item/WI-missing": 404,
        f"/bids/{bid}/execution/items/not-a-kind/missing": 404,
        f"/bids/{bid}/execution/source/DDP-missing": 404,
    }
    for path, expected in checks.items():
        status, _html = _get(module, path)
        assert status == expected


def test_execution_html_hides_storage_vocabulary_and_readiness_is_advisory(
    ui_app: tuple[ModuleType, dict[str, str]],
) -> None:
    module, ids = ui_app
    bid = ids["bid"]
    paths = [
        f"/bids/{bid}/execution",
        f"/bids/{bid}/execution/actions?state=all",
        f"/bids/{bid}/execution/returnables?state=all",
        f"/bids/{bid}/execution/items/work-item/{ids['work']}",
        f"/bids/{bid}/execution/source/{ids['finding']}",
        f"/bids/{bid}/execution/readiness",
    ]
    for path in paths:
        status, html = _get(module, path)
        assert status == 200
        visible = _without_hrefs(html)
        assert not any(prefix in visible for prefix in ("WI-", "DEL-", "DDP-", "BER-"))
        assert not any(
            raw in visible for raw in ("NOT_STARTED", "IN_PROGRESS", "NOT_REQUIRED", "WORK_ITEM")
        )

    readiness = _get(module, f"/bids/{bid}/execution/readiness")[1].lower()
    assert "advisory" in readiness
    assert "<form" not in readiness
    assert "<button" not in readiness


def test_bid_overview_links_to_execution(ui_app: tuple[ModuleType, dict[str, str]]) -> None:
    module, ids = ui_app
    status, html = _get(module, f"/bids/{ids['bid']}")
    assert status == 200
    assert f'href="/bids/{ids["bid"]}/execution"' in html
