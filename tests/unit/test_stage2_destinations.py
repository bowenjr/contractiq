"""Stage 2 — a confirmed finding lands in the register its lifecycle names, once.

Runs a real discovery job (scripted model) against the synthetic Bid used by the Stage 1 route
tests, then accepts findings through the service and checks each existing register.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from core.deliverable_discovery import ProposalReviewDecision
from core.deliverable_discovery_service import ProposalDecisionInput
from core.deliverables import DeliverableCriticality
from tests.unit.test_bid_package_governing_ui import BID_ID
from tests.unit.test_bid_package_governing_ui import governing_ui_app as _app_fixture
from tests.unit.test_deliverable_discovery_job_routes import _include, _start
from tests.unit.test_deliverable_discovery_service import _pdf_bytes

S_BID = "The Bidder shall submit a signed Form of Bid with the proposal."
S_POST = "After award the Vendor shall provide an Inspection and Test Plan for review."
S_MFR = "The Bidder shall obtain the transformer technical data from the manufacturer."
S_PRE = "The Bidder shall confirm receipt of the RFP in writing before bidding opens."
S_COM = "Exceptions to the insurance requirements must be listed in the qualifications schedule."
S_ODD = "Bidders should note that the transformer rating plate is displayed on the cabinet."
S_NA = "This clause is a template placeholder and does not apply to this package of work."
S_COND = "If the transformer is oil filled the Bidder shall provide a spill containment plan."
PASSAGE = "\n".join([S_BID, S_POST, S_MFR, S_PRE, S_COM, S_ODD, S_NA, S_COND])


def _req(title: str, lifecycle: str, quote: str, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "title": title,
        "description": f"{title} (test)",
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
    item.update(overrides)
    return item


REPLY = {
    "requirements": [
        _req("Form of Bid", "BID_RETURNABLE", S_BID, due_date="2026-10-14"),
        _req("Inspection and Test Plan", "POST_AWARD", S_POST),
        _req(
            "Transformer technical data",
            "MANUFACTURER_INPUT",
            S_MFR,
            item_kind="MANUFACTURER_DATA",
            category="Data",
        ),
        _req("RFP receipt confirmation", "PRE_BID_ACTION", S_PRE, item_kind="CLARIFICATION"),
        _req(
            "Insurance exceptions",
            "BID_RETURNABLE",
            S_COM,
            item_kind="COMMERCIAL_QUALIFICATION",
            category="Insurance",
        ),
        _req("Rating plate", "BID_RETURNABLE", "A sentence that is not in the passage text."),
        _req("Template clause", "NOT_APPLICABLE", S_NA, requirement_status="OPTIONAL"),
        _req(
            "Spill containment plan",
            "CONDITIONAL",
            S_COND,
            requirement_status="CONDITIONAL",
            condition_text="If the transformer is oil filled",
        ),
    ]
}


class _Model:
    def complete_json(self, *_args: object, **_kwargs: object) -> object:
        return REPLY


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    yield from _app_fixture.__wrapped__(tmp_path, monkeypatch)


async def _seed(app: ModuleType, folder: str = "Stage2") -> dict[str, str]:
    await _include(app, folder, {"rfp.pdf": _pdf_bytes([PASSAGE])})
    app.discovery_job_runner.llm_client = _Model()
    await _start(app, f"op-{folder}")
    assert app.discovery_job_runner.run_pending() == 1
    return {row["title"]: row["proposal_id"] for row in _queue(app)}


def _queue(app: ModuleType) -> list[dict[str, Any]]:
    return app.deliverable_discovery_service.list_review_queue(BID_ID)


def _accept(app: ModuleType, proposal_id: str, **kwargs: Any) -> dict[str, Any]:
    [result] = app.deliverable_discovery_service.decide(
        BID_ID,
        [ProposalDecisionInput(proposal_id, ProposalReviewDecision.ACCEPTED, **kwargs)],
        "Jason",
    )
    return result


def _row(app: ModuleType, title: str) -> dict[str, Any]:
    return next(r for r in _queue(app) if r["title"] == title)


def test_each_lifecycle_is_classified_and_uncertain_findings_are_kept(app: ModuleType) -> None:
    async def scenario() -> None:
        await _seed(app)
        lifecycles = {r["title"]: r["lifecycle"] for r in _queue(app)}
        assert lifecycles == {
            "Form of Bid": "BID_RETURNABLE",
            "Inspection and Test Plan": "POST_AWARD",
            "Transformer technical data": "MANUFACTURER_INPUT",
            "RFP receipt confirmation": "PRE_BID_ACTION",
            "Insurance exceptions": "BID_RETURNABLE",
            "Rating plate": "NEEDS_REVIEW",  # unverifiable supporting sentence -> retained
            "Template clause": "NOT_APPLICABLE",
            "Spill containment plan": "CONDITIONAL",
        }
        plate = _row(app, "Rating plate")
        assert plate["model_lifecycle"] == "BID_RETURNABLE" and plate["acceptable"] is False
        # nothing exists in any register until a finding is confirmed
        assert app.deliverable_repository.list(BID_ID) == []
        assert app.vendor_document_service.repository.list_packages(BID_ID) == []

    asyncio.run(scenario())


def test_bid_and_post_award_findings_become_deliverables_of_the_right_phase(
    app: ModuleType,
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        assert "error" not in _accept(app, ids["Form of Bid"])
        assert "error" not in _accept(app, ids["Inspection and Test Plan"])
        items = {r["title"]: r for r in app.deliverable_repository.list(BID_ID)}
        assert items["Form of Bid"]["lifecycle_phase"] == "WITH_BID"
        assert items["Inspection and Test Plan"]["lifecycle_phase"] == "POST_AWARD"
        assert items["Form of Bid"]["criticality"] == "MANDATORY"
        # the model's printed-looking date is not in the passage, so it is never applied
        assert items["Form of Bid"]["fixed_due_date"] is None
        destination = _row(app, "Form of Bid")["destination"]
        assert destination["destination_kind"] == "DELIVERABLE" and destination["reused"] == 0

    asyncio.run(scenario())


def test_manufacturer_input_is_registered_once_in_the_vendor_document_register(
    app: ModuleType,
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        result = _accept(app, ids["Transformer technical data"])
        assert result["destination"] == "VENDOR_DOCUMENT"
        repository = app.vendor_document_service.repository
        [package] = repository.list_packages(BID_ID)
        assert package.package_code == "RFP-MFR-INPUTS"
        [requirement] = repository.list_requirements(package.package_id)
        assert requirement.deliverable_title == "Transformer technical data"
        assert f"[DISC:{ids['Transformer technical data']}]" in requirement.source_row_reference
        assert app.deliverable_repository.list(BID_ID) == []  # not also a Deliverable

    asyncio.run(scenario())


def test_commercial_qualification_adds_evidence_to_the_topic_and_keeps_its_disposition(
    app: ModuleType,
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        result = _accept(app, ids["Insurance exceptions"])
        assert result["destination"] == "COMMERCIAL_POSITION"
        detail = app.ops08_repository.detail(result["record_id"])
        assert detail["topic_key"] == "INSURANCE"
        assert "Insurance exceptions" in detail["supporting_evidence"]
        assert detail["disposition"] == "NOT_REVIEWED"  # discovery never decides a position
        assert detail["current_version"] == 2

    asyncio.run(scenario())


def test_pre_bid_action_becomes_one_work_item_without_an_invented_deadline(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        result = _accept(app, ids["RFP receipt confirmation"])
        assert result["destination"] == "WORK_ITEM"
        item = app.work_item_service.get_work_item(result["record_id"])
        assert item.bid_id == BID_ID and item.due_date is None
        assert S_PRE in (item.details or "")

    asyncio.run(scenario())


def test_not_applicable_and_needs_review_cannot_be_accepted_but_can_be_rejected(
    app: ModuleType,
) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        for title in ("Template clause", "Rating plate"):
            result = _accept(app, ids[title])
            assert "error" in result
        assert app.deliverable_repository.list(BID_ID) == []
        [rejected] = app.deliverable_discovery_service.decide(
            BID_ID,
            [
                ProposalDecisionInput(
                    ids["Template clause"],
                    ProposalReviewDecision.REJECTED,
                    "Confirmed not applicable",
                )
            ],
            "Jason",
        )
        assert "error" not in rejected
        assert _row(app, "Template clause")["current_decision"] == "REJECTED"

    asyncio.run(scenario())


def test_conditional_item_needs_a_phase_then_carries_its_condition(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        assert "Choose whether" in _accept(app, ids["Spill containment plan"])["error"]
        [moved] = app.deliverable_discovery_repository.reclassify(
            BID_ID,
            [ids["Spill containment plan"]],
            "CONDITIONAL",
            "Applies with the bid",
            "Jason",
            applies_at="WITH_BID",
        )
        assert "error" not in moved
        assert "error" not in _accept(app, ids["Spill containment plan"])
        [item] = [
            r
            for r in app.deliverable_repository.list(BID_ID)
            if r["title"] == "Spill containment plan"
        ]
        assert item["criticality"] == DeliverableCriticality.CONDITIONAL.value
        assert item["lifecycle_phase"] == "WITH_BID"
        assert item["condition_text"] == "If the transformer is oil filled"

    asyncio.run(scenario())


def test_reclassifying_a_needs_review_finding_makes_it_acceptable(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        app.deliverable_discovery_repository.reclassify(
            BID_ID, [ids["Rating plate"]], "POST_AWARD", "Read in context: due after award", "Jason"
        )
        row = _row(app, "Rating plate")
        assert row["lifecycle"] == "POST_AWARD" and row["lifecycle_source"] == "reviewer"
        assert row["model_lifecycle"] == "BID_RETURNABLE" and row["acceptable"] is True
        assert "error" not in _accept(app, ids["Rating plate"])

    asyncio.run(scenario())


def test_a_second_finding_of_the_same_deliverable_is_linked_not_duplicated(
    app: ModuleType,
) -> None:
    class DuplicateModel:
        def complete_json(self, *_args: object, **_kwargs: object) -> object:
            return {
                "requirements": [
                    _req("Form of Bid", "BID_RETURNABLE", S_BID),
                    _req(
                        "Form of Bid",
                        "CONDITIONAL",
                        S_COND,
                        requirement_status="CONDITIONAL",
                        condition_text="If the transformer is oil filled",
                        applies_at="WITH_BID",
                    ),
                ]
            }

    async def scenario() -> None:
        await _include(app, "Dup", {"rfp.pdf": _pdf_bytes([PASSAGE])})
        app.discovery_job_runner.llm_client = DuplicateModel()
        await _start(app, "op-dup")
        assert app.discovery_job_runner.run_pending() == 1
        first, second = sorted(_queue(app), key=lambda r: r["lifecycle"])  # BID_RETURNABLE first
        assert first["title"] == second["title"] == "Form of Bid"
        one = _accept(app, first["proposal_id"])
        two = _accept(app, second["proposal_id"])
        assert "error" not in one and "error" not in two
        assert two["reused"] is True and two["deliverable_id"] == one["deliverable_id"]
        assert len(app.deliverable_repository.list(BID_ID)) == 1
        linked = _row_by_id(app, second["proposal_id"])
        assert linked["destination"]["reused"] == 1
        assert linked["linked_deliverable_id"] == one["deliverable_id"]

    asyncio.run(scenario())


def _row_by_id(app: ModuleType, proposal_id: str) -> dict[str, Any]:
    return next(r for r in _queue(app) if r["proposal_id"] == proposal_id)


def test_retry_after_a_failure_between_the_steps_creates_no_second_record(
    app: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.deliverable_discovery_service as service_module

    async def scenario() -> None:
        ids = await _seed(app)
        real = service_module.accept_into_record
        calls = {"n": 0}

        def flaky(*args: Any, **kwargs: Any) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("injected failure after the register record was created")
            real(*args, **kwargs)

        monkeypatch.setattr(service_module, "accept_into_record", flaky)
        first = _accept(app, ids["RFP receipt confirmation"])
        assert "injected failure" in first["error"]
        assert _row(app, "RFP receipt confirmation")["current_decision"] == "PENDING"
        second = _accept(app, ids["RFP receipt confirmation"])
        assert "error" not in second
        titles = [i.title for i in app.work_item_service.repository.list(BID_ID)]
        assert titles.count("RFP receipt confirmation") == 1

    asyncio.run(scenario())


def test_bulk_accept_of_mixed_destinations_reports_each_finding(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        chosen = [
            "Form of Bid",
            "Transformer technical data",
            "Template clause",
            "RFP receipt confirmation",
        ]
        results = app.deliverable_discovery_service.decide(
            BID_ID,
            [ProposalDecisionInput(ids[t], ProposalReviewDecision.ACCEPTED) for t in chosen],
            "Jason",
        )
        kinds = [r.get("destination") or "error" for r in results]
        assert kinds == ["DELIVERABLE", "VENDOR_DOCUMENT", "error", "WORK_ITEM"]
        assert _row(app, "Template clause")["current_decision"] == "PENDING"

    asyncio.run(scenario())


def test_rediscovery_adds_the_new_source_to_the_existing_finding(app: ModuleType) -> None:
    async def scenario() -> None:
        ids = await _seed(app)
        before = len(_queue(app))
        _accept(app, ids["Form of Bid"])
        await _include(app, "Second", {"addendum.pdf": _pdf_bytes([PASSAGE])})
        await _start(app, "op-second")
        assert app.discovery_job_runner.run_pending() >= 1
        queue = _queue(app)
        assert len(queue) == before  # nothing duplicated
        assert len(_row(app, "Form of Bid")["sources"]) == 2  # both documents cited
        links = app.deliverable_repository.links(_row(app, "Form of Bid")["linked_deliverable_id"])
        assert len([link for link in links if link["relation"] == "SOURCE_RECEIVED_FILE"]) == 2

    asyncio.run(scenario())
