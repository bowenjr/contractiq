from __future__ import annotations

from pathlib import Path

from core.bid_package_intake import (
    DirectiveCreate,
    DirectiveMateriality,
    FileDocumentRelationship,
)
from core.bid_repository import BidRepository
from tests.unit.test_bid_package_storage import BID_ID, NOW, _command
from tests.unit.test_deliverable_discovery_promotion import (
    _accepted,
    _link,
    _promote,
    _version,
)


def _record_directive(
    fx: dict[str, object], version_id: str, operation_id: str
) -> dict[str, object]:
    return fx["package"].record_directive(
        BID_ID,
        fx["release_id"],
        DirectiveCreate(
            directive_type="CHANGE",
            description="Revised received-file requirement.",
            materiality=DirectiveMateriality.MATERIAL,
            target_document_version_id=version_id,
            operation_id=operation_id,
        ),
        "Jason",
    )


def test_linked_received_file_flags_accepted_proposal_and_deliverable_without_mutation(
    tmp_path: Path,
) -> None:
    fx = _accepted(tmp_path)
    version_id = _version(fx)
    _link(fx, version_id)
    directive = _record_directive(fx, version_id, "received-file-directive")
    proposal_id = fx["proposal"]["proposal_id"]
    deliverable_id = fx["deliverable_id"]
    file_id = fx["citation"]["received_file_id"]
    review_before = dict(fx["discovery_repository"].current_decision(proposal_id))
    deliverable_before = next(
        dict(row)
        for row in fx["deliverables"].list(BID_ID)
        if row["deliverable_id"] == deliverable_id
    )

    flagged = fx["discovery"].detect_addendum_impacts(BID_ID, "Jason")

    assert set(flagged) == {proposal_id, deliverable_id}
    flags = fx["discovery_repository"].list_addendum_flags(BID_ID)
    assert len(flags) == 2
    assert {row["directive_id"] for row in flags} == {directive["directive_id"]}
    for row in flags:
        assert file_id in row["rationale"]
        assert "analysis.pdf" in row["rationale"]
        assert version_id in row["rationale"]
    assert fx["discovery_repository"].current_decision(proposal_id) == review_before
    assert (
        next(
            row
            for row in fx["deliverables"].list(BID_ID)
            if row["deliverable_id"] == deliverable_id
        )
        == deliverable_before
    )

    assert fx["discovery"].detect_addendum_impacts(BID_ID, "Jason") == []
    assert len(fx["discovery_repository"].list_addendum_flags(BID_ID)) == 2


def test_supporting_evidence_received_file_is_not_flagged(tmp_path: Path) -> None:
    fx = _accepted(tmp_path)
    version_id = _version(fx)
    _link(fx, version_id, FileDocumentRelationship.SUPPORTING_EVIDENCE)
    _record_directive(fx, version_id, "supporting-evidence-directive")

    assert fx["discovery"].detect_addendum_impacts(BID_ID, "Jason") == []
    assert fx["discovery_repository"].list_addendum_flags(BID_ID) == []


def test_received_file_link_from_another_bid_is_not_flagged(tmp_path: Path) -> None:
    fx = _accepted(tmp_path)
    version_id = _version(fx)
    bids = BidRepository(fx["database"])
    original = bids.get_bid(BID_ID)
    assert original is not None
    other_bid_id = "B-2026-0012"
    bids.create_bid(original.model_copy(update={"bid_id": other_bid_id}))
    preview = fx["package"].preview(other_bid_id, "landing")
    release = fx["package"].register_release(
        _command(preview.source_fingerprint, "other-bid-release").model_copy(
            update={"bid_id": other_bid_id}
        ),
        "Jason",
    )
    other_file = next(
        row
        for row in fx["package"].release_detail(other_bid_id, release["release_id"])["files"]
        if row["original_filename"] == "analysis.pdf"
    )
    with fx["database"]._conn() as conn:
        conn.execute(
            "INSERT INTO bid_received_file_document_links VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "RFDL-cross-bid-test",
                other_file["file_id"],
                version_id,
                "EXACT_BYTES",
                None,
                "cross-bid-link",
                "Jason",
                NOW.isoformat(),
                "{}",
            ),
        )

    assert fx["discovery_repository"].received_file_ids_for_version(BID_ID, version_id) == []
    assert fx["discovery_repository"].received_file_ids_for_version(other_bid_id, version_id) == [
        {
            "file_id": other_file["file_id"],
            "original_filename": "analysis.pdf",
            "relationship": "EXACT_BYTES",
        }
    ]
    assert (
        fx["discovery"].flag_addendum_effect(
            BID_ID,
            "DIR-cross-bid-test",
            version_id,
            "Cross-Bid isolation test.",
            "Jason",
        )
        == []
    )


def test_promoted_deliverable_matching_received_file_and_version_is_flagged_once(
    tmp_path: Path,
) -> None:
    fx = _accepted(tmp_path)
    version_id = _version(fx)
    _link(fx, version_id)
    _promote(fx, version_id)
    directive = _record_directive(fx, version_id, "promoted-directive")

    flagged = fx["discovery"].detect_addendum_impacts(BID_ID, "Jason")

    assert flagged.count(fx["proposal"]["proposal_id"]) == 1
    assert flagged.count(fx["deliverable_id"]) == 1
    deliverable_flags = [
        row
        for row in fx["discovery_repository"].list_addendum_flags(BID_ID)
        if row["directive_id"] == directive["directive_id"]
        and row["deliverable_id"] == fx["deliverable_id"]
    ]
    assert len(deliverable_flags) == 1
