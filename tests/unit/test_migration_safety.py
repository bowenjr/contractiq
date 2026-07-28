from pathlib import Path

from core.bid_repository import BidRepository
from core.database import Database


def test_bid_migration_preserves_pre_existing_documents(tmp_path: Path) -> None:
    db = Database(tmp_path / "pre-existing.db")
    db.create_document({"id": "DOC-BEFORE-1", "filename": "before-one.pdf"})
    db.create_document({"id": "DOC-BEFORE-2", "filename": "before-two.pdf"})
    count_before = len(db.get_all_documents())

    BidRepository(db)

    first_document = db.get_document("DOC-BEFORE-1")
    second_document = db.get_document("DOC-BEFORE-2")
    assert first_document is not None
    assert second_document is not None
    assert first_document["bid_id"] is None
    assert second_document["bid_id"] is None
    assert len(db.get_all_documents()) == count_before


def test_provenance_retrofit_accepts_legacy_obligation_values(tmp_path: Path) -> None:
    db = Database(tmp_path / "legacy-obligation.db")
    db.create_document({"id": "DOC-LEGACY", "filename": "legacy.pdf"})

    db.save_obligations(
        "DOC-LEGACY",
        [
            {
                "party": "Customer",
                "obligation_type": "Payment",
                "description": "Pay within the agreed period",
                "trigger": "after customer acceptance",
            }
        ],
    )

    [obligation] = db.get_obligations("DOC-LEGACY")
    assert obligation["obligation_type"] == "PAY"
    assert obligation["trigger"] == "after customer acceptance"
