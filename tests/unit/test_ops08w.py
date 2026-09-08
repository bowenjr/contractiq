from __future__ import annotations

import asyncio
from pathlib import Path

from scripts.asgi_acceptance_ops08w import main


def test_connected_bid_workflow_acceptance() -> None:
    asyncio.run(main())


def test_workflow_copy_exposes_business_actions_and_no_post_award_flow() -> None:
    root = Path(__file__).parents[2]
    bid = (root / "templates" / "bid_detail.html").read_text()
    package = (root / "templates" / "vendor_document_package.html").read_text()
    role = (root / "templates" / "role_framework.html").read_text()

    for label in (
        "Add manufacturer/equipment package",
        "Create linked risk",
        "Start negotiation",
        "Request approval",
        "Derived proposal inputs",
        "No manufacturer or supplier coverage has been recorded for this Bid.",
    ):
        assert label in bid
    assert "Vendor-document responsibilities, confirmations and exceptions" in package
    assert "Back to Bid Manufacturers &amp; Coverage" in package
    assert "<h1>My Role</h1>" in role
    assert "Create role profile" not in role
    assert "post-award submission" not in bid.casefold()


def test_my_day_uses_one_primary_bid_summary_and_business_status() -> None:
    root = Path(__file__).parents[2]
    template = (root / "templates" / "my_day.html").read_text()
    assert 'data-bid-summary="{{ summary.bid.bid_id }}"' in template
    assert "Commercial review incomplete" in (root / "app.py").read_text()
    assert "{{ row.severity }}" not in template
