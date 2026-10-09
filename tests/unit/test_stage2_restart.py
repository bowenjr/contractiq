"""Stage 2 — a classified discovery job survives restart and cancellation unchanged."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from core.deliverable_discovery_extraction import RawUnit
from core.deliverable_discovery_jobs import DiscoveryJobRunner
from core.deliverable_discovery_llm import analyse_units
from tests.unit.test_bid_package_storage import BID_ID
from tests.unit.test_deliverable_discovery_jobs_recovery import _Clock, _jobs

FIRST = "Submit the completed signed Bid Form to Procurement with the complete offer now."
SECOND = "After award the Vendor shall provide the final data book within thirty days."


def _item(title: str, lifecycle: str, quote: str) -> dict[str, Any]:
    return {
        "title": title,
        "description": title,
        "lifecycle": lifecycle,
        "requirement_status": "REQUIRED",
        "item_kind": "SUBMISSION_DOCUMENT",
        "category": "Form",
        "condition_text": None,
        "applies_at": None,
        "responsible_party": None,
        "timing_text": None,
        "due_date": None,
        "dependencies": None,
        "confidence": "HIGH",
        "review_reason": None,
        "source_section": None,
        "source_quote": quote,
    }


class _Client:
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, prompt: str, **_: object) -> dict[str, Any]:
        self.calls += 1
        if "After award" in prompt:
            return {"requirements": [_item("Final data book", "POST_AWARD", SECOND)]}
        return {"requirements": [_item("Bid Form", "BID_RETURNABLE", FIRST)]}


def test_recorded_classified_passage_is_kept_and_not_recalled_after_a_crash(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    client = _Client()
    fx, service = _jobs(tmp_path, [FIRST, SECOND], client, clock)  # type: ignore[arg-type]
    job = service.start_job(BID_ID, "stage2-restart", "Jason")
    file_row = service.jobs.files(job["job_id"])[0]
    owner = "crashed-runner"
    assert service.jobs.claim(job["job_id"], owner, clock(), clock() + timedelta(minutes=15))
    service.jobs.set_totals(job["job_id"], owner, 2, clock(), clock() + timedelta(minutes=15))
    # the crashed runner had already analysed (and durably recorded) page 1 with the real parser
    unit = RawUnit("PAGE", "p.1", FIRST, True)
    analysed = analyse_units(client, [("k", unit)])
    client.calls = 0
    service.jobs.record_passage(
        job["job_id"],
        owner,
        {
            "passage_key": f"{file_row['received_file_id']}|PAGE|p.1",
            "received_file_id": file_row["received_file_id"],
            "locator_type": "PAGE",
            "locator": "p.1",
            "outcome": "ANALYSED",
            "candidates_json": json.dumps(
                [c.model_dump(mode="json") for c in analysed.candidates], sort_keys=True
            ),
            "invalid_candidate_count": 0,
            "safe_reason": None,
        },
        False,
        clock(),
        clock() + timedelta(minutes=15),
    )
    clock.advance(minutes=20)  # the dead runner's lease expires

    result = DiscoveryJobRunner(service, client, pid_alive=lambda pid: False).process_job(
        job["job_id"]
    )
    assert result and result["state"] == "SUCCEEDED"
    assert client.calls == 1  # only the page that was not yet analysed
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        rows = conn.execute(
            """SELECT p.title,c.lifecycle,c.classified_by
            FROM deliverable_discovery_proposals p
            JOIN deliverable_discovery_proposal_classification c ON c.proposal_id=p.proposal_id
            ORDER BY p.title"""
        ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("Bid Form", "BID_RETURNABLE", "MODEL"),
        ("Final data book", "POST_AWARD", "MODEL"),
    ]
