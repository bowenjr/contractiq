"""'Waiting on others' shows business titles and direct links, keeping record IDs as evidence."""

from __future__ import annotations

import asyncio
import re
from types import ModuleType

from core.schemas import Bid
from tests.unit.test_gate_approval_resolve_path import (
    _request,
)
from tests.unit.test_gate_approval_resolve_path import (
    gate_approval_app as gate_approval_app,
)


def test_commercial_positions_render_titles_links_and_keep_ids(
    gate_approval_app: ModuleType, valid_bid: Bid
) -> None:
    module = gate_approval_app
    status, _, body = asyncio.run(
        _request(
            module.app,
            "POST",
            "/commercial",
            {"bid_id": valid_bid.bid_id, "operation": "initialize", "default_owner": "Owner"},
        )
    )
    assert status == 303, body[:300]
    _, _, body = asyncio.run(_request(module.app, "GET", f"/bids/{valid_bid.bid_id}"))
    page = body.decode()
    waiting = page[page.index("Waiting on others") :]
    assert "Pending decision CP-" not in waiting
    links = re.findall(
        r'<a href="(/commercial/CP-[^"]+)" title="Record ID: (CP-[^"]+)" '
        r'data-record-id="(CP-[^"]+)">([^<]+)</a> · ([^<]+)</p>',
        waiting,
    )
    assert len(links) >= 10
    for href, record_id, data_id, title, reason in links:
        assert href == f"/commercial/{record_id}" and record_id == data_id
        assert not title.startswith("CP-") and title.strip()
        assert reason.strip()
    # The link lands on the position's own detail page.
    status, _, detail = asyncio.run(_request(module.app, "GET", links[0][0]))
    assert status == 200 and links[0][3].encode() in detail
