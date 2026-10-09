"""Stage 1 / Fix 5 (and the evidence half of Fix 6): a due date is stated, calculated, proposed or
unknown; a model's guess never becomes the customer's deadline; quotations are verified."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

from core.date_integrity import (
    DueDateBasis,
    classify_due_date,
    date_is_stated,
    deliverable_date_note,
    evidence_window,
    locate_quote,
)
from core.deliverable_discovery import ProposalReviewDecision
from core.deliverable_discovery_evidence import DATES_TABLE, UNVERIFIED
from core.deliverable_discovery_service import ProposalDecisionInput
from core.schemas import Bid
from tests.unit.test_bid_package_storage import BID_ID
from tests.unit.test_deliverable_discovery_run_outcomes import _fixture
from tests.unit.test_deliverable_discovery_service import _pdf_bytes

PASSAGE = (
    "Tenderers shall submit the completed Bid Form to Procurement no later than 15 October 2026. "
    "Late submissions are not accepted."
)


# --------------------------------------------------------------------- classification units


@pytest.mark.parametrize(
    "written",
    ["2026-10-15", "15 October 2026", "October 15, 2026", "Oct. 15th, 2026", "15-Oct-2026"],
)
def test_a_date_printed_in_the_passage_is_stated(written: str) -> None:
    assert date_is_stated(f"Submit by {written} at noon.", date(2026, 10, 15))


def test_a_date_not_in_the_passage_is_not_stated() -> None:
    assert not date_is_stated("Submit promptly after award.", date(2026, 10, 15))
    assert not date_is_stated("Submit by 16 October 2026.", date(2026, 10, 15))


def test_stated_date_is_used() -> None:
    result = classify_due_date(PASSAGE, date(2026, 10, 15), None)
    assert (result.basis, result.effective) == (DueDateBasis.STATED, date(2026, 10, 15))


def test_a_model_date_not_found_in_the_passage_is_only_a_proposal() -> None:
    result = classify_due_date("Provide the schedule promptly.", date(2026, 11, 1), None)
    assert result.basis is DueDateBasis.PROPOSED
    assert result.effective is None
    assert result.proposed == date(2026, 11, 1)


def test_no_date_at_all_is_unknown() -> None:
    result = classify_due_date("Provide the schedule.", None, None)
    assert (result.basis, result.effective, result.proposed) == (DueDateBasis.UNKNOWN, None, None)


def test_calendar_day_rule_is_calculated_from_a_recorded_trigger() -> None:
    passage = "Submit the sample 14 calendar days before closing."
    result = classify_due_date(
        passage,
        None,
        "14 calendar days before closing",
        {"closing": date(2026, 10, 15)},
    )
    assert (result.basis, result.effective) == (DueDateBasis.CALCULATED, date(2026, 10, 1))


def test_calendar_day_rule_without_a_recorded_trigger_is_not_calculated() -> None:
    result = classify_due_date(
        "Submit 14 calendar days before closing.",
        date(2026, 10, 1),
        "14 calendar days before closing",
        {},
    )
    assert result.basis is DueDateBasis.PROPOSED and result.effective is None


def test_business_day_rules_are_never_calculated() -> None:
    result = classify_due_date(
        "Submit 5 business days after issue.",
        date(2026, 10, 8),
        "5 business days after issue",
        {"issue": date(2026, 10, 1)},
    )
    assert result.basis is DueDateBasis.PROPOSED and result.effective is None


def test_rule_text_not_in_the_passage_is_discarded() -> None:
    result = classify_due_date(
        "Submit promptly.", None, "30 calendar days before closing", {"closing": date(2026, 10, 15)}
    )
    assert result.basis is DueDateBasis.UNKNOWN and result.rule_text is None


# --------------------------------------------------------------------------- quotations


def test_quote_is_located_ignoring_case_and_spacing() -> None:
    quote = "submit the  completed bid form\nto procurement"
    span = locate_quote(PASSAGE, quote)
    assert span is not None
    assert PASSAGE[span[0] : span[1]].lower().split() == quote.lower().split()


def test_a_quote_not_in_the_passage_is_refused() -> None:
    assert locate_quote(PASSAGE, "Tenderers shall also submit a performance bond") is None
    assert locate_quote(PASSAGE, None) is None
    assert locate_quote(PASSAGE, "short") is None


def test_evidence_window_is_the_quote_with_context_not_the_page_top() -> None:
    filler = "Page header and general conditions. " * 40
    passage = f"{filler}The Supplier shall provide a type test report. {filler}"
    span = locate_quote(passage, "The Supplier shall provide a type test report.")
    assert span is not None
    window = evidence_window(passage, span)
    assert "type test report" in window and len(window) < len(passage)


# ------------------------------------------------------------------ deliverable date notes


def test_date_note_reads_the_recorded_basis() -> None:
    description = "Proposed from PAGE p.2 of rfp.pdf: text\nDue date basis: not stated."
    assert deliverable_date_note(description, None, True) == "not stated"


def test_legacy_discovery_deliverable_with_a_date_is_reported_unverified() -> None:
    note = deliverable_date_note("Proposed from PAGE p.2 of rfp.pdf: x", date(2026, 10, 1), True)
    assert note is not None and "not checked" in note


def test_manual_deliverable_has_no_note() -> None:
    assert deliverable_date_note("Hand entered", date(2026, 10, 1), False) is None


# ------------------------------------------------------------- Bid closing time and zone


def _bid(**changes: object) -> Bid:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    base: dict[str, object] = {
        "bid_id": "B-2026-0001",
        "customer": "Goldboro",
        "customer_type": "epc",
        "project_name": "Goldboro Substation",
        "sales_owner": "S",
        "bc_owner": "B",
        "release_date": date(2026, 9, 1),
        "customer_due_date": date(2026, 10, 15),
        "internal_due_date": date(2026, 10, 10),
        "estimated_value": 1,
        "classification": "level_2",
        "created_at": now,
        "updated_at": now,
    }
    return Bid.model_validate({**base, **changes})


def test_closing_instant_is_none_until_time_and_zone_are_recorded() -> None:
    bid = _bid()
    assert bid.customer_closing_instant() is None
    assert "not recorded" in bid.customer_closing_label()


def test_eastern_noon_closing_converts_to_utc_with_daylight_time() -> None:
    bid = _bid(customer_due_time=time(12, 0), customer_due_timezone="America/Toronto")
    assert bid.customer_closing_instant() == datetime(2026, 10, 15, 16, 0, tzinfo=UTC)
    assert "12:00" in bid.customer_closing_label() and "EDT" in bid.customer_closing_label()


def test_the_same_local_time_after_the_clocks_change_is_five_hours_from_utc() -> None:
    bid = _bid(
        customer_due_date=date(2026, 12, 1),
        customer_due_time=time(12, 0),
        customer_due_timezone="America/Toronto",
    )
    assert bid.customer_closing_instant() == datetime(2026, 12, 1, 17, 0, tzinfo=UTC)


def test_time_without_zone_and_zone_without_time_are_refused() -> None:
    with pytest.raises(ValueError, match="together"):
        _bid(customer_due_time=time(12, 0))
    with pytest.raises(ValueError, match="together"):
        _bid(customer_due_timezone="America/Toronto")


def test_unknown_zone_is_refused_with_a_hint() -> None:
    with pytest.raises(ValueError, match="IANA"):
        _bid(customer_due_time=time(12, 0), customer_due_timezone="Eastern")


def test_recording_the_closing_writes_an_audit_row_and_clearing_removes_the_time(
    tmp_path: Path,
) -> None:
    from core.bid_repository import BidRepository
    from core.database import Database

    repository = BidRepository(Database(tmp_path / "bids.db"))
    repository.create_bid(_bid())
    saved = repository.record_customer_closing(
        "B-2026-0001", time(12, 0), "America/Toronto", "Jason", "RFP p.1"
    )
    assert saved.customer_closing_instant() == datetime(2026, 10, 15, 16, 0, tzinfo=UTC)
    stored = repository.get_bid("B-2026-0001")
    assert stored is not None and stored.customer_due_timezone == "America/Toronto"
    audit = [a for a in repository.list_audit("B-2026-0001") if "closing" in a.action]
    assert len(audit) == 1 and "RFP p.1" in audit[0].detail

    repository.record_customer_closing("B-2026-0001", None, None, "Jason", "cleared")
    cleared = repository.get_bid("B-2026-0001")
    assert cleared is not None and cleared.customer_closing_instant() is None


def test_existing_bids_database_gains_the_columns_without_losing_rows(tmp_path: Path) -> None:
    from core.bid_repository import BidRepository
    from core.database import Database

    path = tmp_path / "old.db"
    repository = BidRepository(Database(path))
    repository.create_bid(_bid())
    with sqlite3.connect(path) as conn:  # simulate a database created before the columns existed
        conn.execute("ALTER TABLE bids DROP COLUMN customer_due_time")
        conn.execute("ALTER TABLE bids DROP COLUMN customer_due_timezone")
    migrated = BidRepository(Database(path))
    bid = migrated.get_bid("B-2026-0001")
    assert bid is not None and bid.customer_due_time is None
    migrated.record_customer_closing("B-2026-0001", time(9, 0), "America/Toronto", "Jason", "RFP")


# ------------------------------------------------------- discovery proposals end to end


class ScriptedClient:
    """Returns one candidate per passage, with the date and quote it is told to claim."""

    def __init__(self, due_date: str | None, rule: str | None = None, quote: str | None = None):
        self.due_date, self.rule, self.quote = due_date, rule, quote
        self.calls = 0

    def complete_json(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        return {
            "candidates": [
                {
                    "title": "Signed Bid Form",
                    "category": "FORM",
                    "criticality": "REQUIRED",
                    "condition_text": None,
                    "due_date": self.due_date,
                    "requested_format": "PDF",
                    "requested_recipient": "Procurement",
                    "proposed_owner": None,
                    "due_date_text": self.rule,
                    "source_quote": self.quote,
                }
            ]
        }


def _run(tmp_path: Path, passage: str, client: ScriptedClient):
    fx = _fixture(tmp_path, {"rfp.pdf": _pdf_bytes([passage])}, client)  # type: ignore[arg-type]
    result = fx["service"].run_discovery(BID_ID, "date-run", "Jason")  # type: ignore[union-attr]
    queue = fx["service"].list_review_queue(BID_ID)  # type: ignore[union-attr]
    return fx, result, queue


def test_stated_date_is_stored_and_applied_when_the_finding_is_accepted(tmp_path: Path) -> None:
    fx, _, queue = _run(tmp_path, PASSAGE, ScriptedClient("2026-10-15"))
    assert len(queue) == 1
    row = queue[0]
    assert (row["due_date_basis"], row["due_date"]) == ("STATED", "2026-10-15")

    service = fx["service"]
    out = service.decide(  # type: ignore[union-attr]
        BID_ID,
        [ProposalDecisionInput(row["proposal_id"], ProposalReviewDecision.ACCEPTED)],
        "Jason",
    )
    assert "error" not in out[0], out
    item = next(i for i in fx["deliverables"].list(BID_ID))  # type: ignore[union-attr]
    assert str(item["fixed_due_date"]) == "2026-10-15"
    assert "stated in the customer's document" in item["description"]


def test_a_guessed_date_is_not_applied_and_the_deliverable_says_so(tmp_path: Path) -> None:
    passage = "Tenderers shall submit the completed Bid Form to Procurement promptly after award."
    fx, _, queue = _run(tmp_path, passage, ScriptedClient("2026-11-30"))
    row = queue[0]
    assert (row["due_date_basis"], row["due_date"], row["proposed_due_date"]) == (
        "PROPOSED",
        None,
        "2026-11-30",
    )
    fx["service"].decide(  # type: ignore[union-attr]
        BID_ID,
        [ProposalDecisionInput(row["proposal_id"], ProposalReviewDecision.ACCEPTED)],
        "Jason",
    )
    item = fx["deliverables"].list(BID_ID)[0]  # type: ignore[union-attr]
    assert not item["fixed_due_date"]
    assert "not confirmed" in item["description"]


def test_a_reviewer_confirmed_date_is_applied_and_labelled_as_confirmed(tmp_path: Path) -> None:
    passage = "Tenderers shall submit the completed Bid Form to Procurement promptly after award."
    fx, _, queue = _run(tmp_path, passage, ScriptedClient("2026-11-30"))
    fx["service"].decide(  # type: ignore[union-attr]
        BID_ID,
        [
            ProposalDecisionInput(
                queue[0]["proposal_id"],
                ProposalReviewDecision.ACCEPTED,
                due_date=date(2026, 12, 1),
            )
        ],
        "Jason",
    )
    item = fx["deliverables"].list(BID_ID)[0]  # type: ignore[union-attr]
    assert str(item["fixed_due_date"]) == "2026-12-01"
    assert "confirmed by Jason" in item["description"]


def test_a_finding_without_a_date_is_unknown(tmp_path: Path) -> None:
    _, _, queue = _run(tmp_path, PASSAGE, ScriptedClient(None))
    assert (queue[0]["due_date_basis"], queue[0]["due_date"]) == ("UNKNOWN", None)


def test_a_verbatim_quote_becomes_the_evidence_and_a_made_up_one_is_dropped(
    tmp_path: Path,
) -> None:
    quote = "Tenderers shall submit the completed Bid Form to Procurement"
    _, _, queue = _run(tmp_path, PASSAGE, ScriptedClient(None, quote=quote))
    source = queue[0]["sources"][0]
    assert source["quote"] and source["quote"].lower().startswith("tenderers shall submit")

    (tmp_path / "second").mkdir()
    _, _, queue = _run(
        tmp_path / "second", PASSAGE, ScriptedClient(None, quote="The Supplier must post a bond.")
    )
    assert queue[0]["sources"][0]["quote"] is None
    assert queue[0]["sources"][0]["excerpt"].startswith("Tenderers shall submit")


def test_findings_recorded_before_date_evidence_existed_report_unverified(tmp_path: Path) -> None:
    fx, _, queue = _run(tmp_path, PASSAGE, ScriptedClient("2026-10-15"))
    assert queue[0]["due_date_basis"] == "STATED"
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        conn.execute("DROP TRIGGER ddpd_no_delete")
        conn.execute(f"DELETE FROM {DATES_TABLE}")
        conn.commit()
    legacy = fx["service"].list_review_queue(BID_ID)[0]  # type: ignore[union-attr]
    assert legacy["due_date_basis"] == UNVERIFIED
    assert legacy["due_date"] is None  # not trusted as a deadline
    assert legacy["proposed_due_date"] == "2026-10-15"  # but still shown for confirmation


def test_evidence_rows_are_insert_only(tmp_path: Path) -> None:
    fx, _, _ = _run(tmp_path, PASSAGE, ScriptedClient("2026-10-15"))
    with fx["database"]._conn() as conn:  # type: ignore[union-attr]
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(f"UPDATE {DATES_TABLE} SET basis='UNKNOWN'")
