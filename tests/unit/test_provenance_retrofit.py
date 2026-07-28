from collections.abc import Callable
from pathlib import Path

import pytest

from core.database import Database
from core.schemas import Provenance

PROVENANCE_COLUMNS = {
    "prov_created_by",
    "prov_agent_name",
    "prov_model",
    "prov_source_location",
    "prov_created_at",
    "human_confirmed",
    "confirmed_by",
    "confirmed_at",
}
ANALYSIS_TABLES = (
    "clause_findings",
    "scope_items",
    "obligations",
    "negotiation_issues",
)


def _database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "provenance.db")
    db.create_document({"id": "DOC-1", "filename": "contract.pdf"})
    return db


def _clause_results() -> list[dict]:
    return [
        {
            "pillar_id": "money",
            "findings": [
                {
                    "finding": "Payment term",
                    "clause_reference": "Section 4",
                    "source_excerpt": "Net 60",
                    "detail": "Long payment term",
                    "severity": "High",
                    "recommended_action": "Request Net 30",
                    "requires_legal": True,
                }
            ],
        }
    ]


def _scope_items() -> list[dict]:
    return [
        {
            "project_id": "PROJECT-1",
            "requirement_source": "Schedule A",
            "requirement_text": "Supply commissioning services",
            "included_in_quote": True,
            "excluded_in_quote": False,
            "priced": True,
            "owner": "estimating",
            "gap_status": "covered",
            "comments": "Included",
        }
    ]


def _obligations() -> list[dict]:
    return [
        {
            "party": "Supplier",
            "obligation_type": "Payment",
            "description": "Pay the fee",
            "trigger": "fixed date",
            "deadline": "2026-08-31",
            "notice_required": "No",
            "owner": "finance",
            "status": "Open",
        }
    ]


def _negotiation_results() -> list[dict]:
    return [
        {
            "pillar_id": "money",
            "negotiation_points": [
                {
                    "issue": "Payment timing",
                    "priority": "High",
                    "primary_ask": "Net 30",
                    "fallback": "Net 45",
                    "requires_legal": False,
                }
            ],
        }
    ]


def test_default_clause_finding_write_is_ai_unconfirmed(tmp_path: Path) -> None:
    db = _database(tmp_path)

    db.save_clause_findings("DOC-1", _clause_results())

    [finding] = db.get_clause_findings("DOC-1")
    assert finding["prov_created_by"] == "ai"
    assert finding["prov_agent_name"] == "analysis_engine"
    assert finding["human_confirmed"] == 0
    assert finding["confirmed_by"] is None
    assert finding["confirmed_at"] is None


def test_human_authorship_does_not_implicitly_confirm_finding(tmp_path: Path) -> None:
    db = _database(tmp_path)

    db.save_clause_findings(
        "DOC-1",
        _clause_results(),
        provenance=Provenance.from_human("jason"),
    )

    [finding] = db.get_clause_findings("DOC-1")
    assert finding["prov_created_by"] == "human"
    assert finding["prov_agent_name"] == "jason"
    assert finding["human_confirmed"] == 0
    assert finding["confirmed_by"] is None
    assert finding["confirmed_at"] is None


def test_confirm_clause_finding_and_missing_id(tmp_path: Path) -> None:
    db = _database(tmp_path)
    db.save_clause_findings("DOC-1", _clause_results())
    [finding] = db.get_clause_findings("DOC-1")

    assert db.confirm_clause_finding(finding["id"], "jason") is True
    assert db.confirm_clause_finding(999_999, "jason") is False

    [confirmed] = db.get_clause_findings("DOC-1")
    assert confirmed["human_confirmed"] == 1
    assert confirmed["confirmed_by"] == "jason"
    assert confirmed["confirmed_at"] is not None


@pytest.mark.parametrize(
    ("save", "read", "confirm"),
    [
        (
            lambda db: db.save_scope_items("DOC-1", _scope_items()),
            lambda db: db.get_scope_items("DOC-1"),
            lambda db, row_id: db.confirm_scope_item(row_id, "jason"),
        ),
        (
            lambda db: db.save_obligations("DOC-1", _obligations()),
            lambda db: db.get_obligations("DOC-1"),
            lambda db, row_id: db.confirm_obligation(row_id, "jason"),
        ),
        (
            lambda db: db.save_negotiation_issues("DOC-1", _negotiation_results()),
            lambda db: db.get_negotiation_issues("DOC-1"),
            lambda db, row_id: db.confirm_negotiation_issue(row_id, "jason"),
        ),
    ],
    ids=("scope-item", "obligation", "negotiation-issue"),
)
def test_analysis_row_confirmation_round_trip(
    tmp_path: Path,
    save: Callable[[Database], None],
    read: Callable[[Database], list[dict]],
    confirm: Callable[[Database, int], bool],
) -> None:
    db = _database(tmp_path)
    save(db)
    [row] = read(db)
    assert row["prov_created_by"] == "ai"
    assert row["human_confirmed"] == 0

    assert confirm(db, row["id"]) is True

    [confirmed] = read(db)
    assert confirmed["human_confirmed"] == 1
    assert confirmed["confirmed_by"] == "jason"
    assert confirmed["confirmed_at"] is not None


def test_count_unconfirmed_before_and_after_confirmations(tmp_path: Path) -> None:
    db = _database(tmp_path)
    db.save_clause_findings("DOC-1", _clause_results())
    db.save_scope_items("DOC-1", _scope_items())
    db.save_obligations("DOC-1", _obligations())
    db.save_negotiation_issues("DOC-1", _negotiation_results())

    assert db.count_unconfirmed("DOC-1") == {
        "clause_findings": 1,
        "scope_items": 1,
        "obligations": 1,
        "negotiation_issues": 1,
    }

    [finding] = db.get_clause_findings("DOC-1")
    [obligation] = db.get_obligations("DOC-1")
    db.confirm_clause_finding(finding["id"], "jason")
    db.confirm_obligation(obligation["id"], "jason")

    assert db.count_unconfirmed("DOC-1") == {
        "clause_findings": 0,
        "scope_items": 1,
        "obligations": 0,
        "negotiation_issues": 1,
    }


def test_backfill_stamps_rows_with_honest_legacy_provenance(tmp_path: Path) -> None:
    db = _database(tmp_path)
    with db._conn() as conn:
        conn.execute(
            """
            INSERT INTO clause_findings (
                document_id, pillar, topic, prov_created_by, prov_agent_name,
                prov_model, prov_created_at, human_confirmed
            ) VALUES (?, ?, ?, NULL, NULL, NULL, NULL, NULL)
            """,
            ("DOC-1", "money", "Legacy finding"),
        )
        conn.commit()

    db._evolve_provenance_schema()

    [finding] = db.get_clause_findings("DOC-1")
    assert finding["prov_created_by"] == "ai"
    assert finding["prov_agent_name"] == "legacy_import"
    assert finding["prov_model"] is None
    assert finding["prov_created_at"] is not None
    assert finding["human_confirmed"] == 0


def test_migration_is_idempotent_and_preserves_confirmation(tmp_path: Path) -> None:
    db = _database(tmp_path)
    db.save_clause_findings("DOC-1", _clause_results())
    [finding] = db.get_clause_findings("DOC-1")
    db.confirm_clause_finding(finding["id"], "jason")
    [confirmed_before] = db.get_clause_findings("DOC-1")

    db._evolve_provenance_schema()
    db._evolve_provenance_schema()

    with db._conn() as conn:
        for table in ANALYSIS_TABLES:
            column_names = [
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            ]
            for column in PROVENANCE_COLUMNS:
                assert column_names.count(column) == 1

    [confirmed_after] = db.get_clause_findings("DOC-1")
    assert confirmed_after["human_confirmed"] == 1
    assert confirmed_after["confirmed_by"] == "jason"
    assert confirmed_after["confirmed_at"] == confirmed_before["confirmed_at"]
    assert confirmed_after["prov_agent_name"] == "analysis_engine"


def test_existing_read_paths_preserve_business_fields(tmp_path: Path) -> None:
    db = _database(tmp_path)
    db.save_clause_findings("DOC-1", _clause_results())
    db.save_scope_items("DOC-1", _scope_items())
    db.save_obligations("DOC-1", _obligations())
    db.save_negotiation_issues("DOC-1", _negotiation_results())

    [finding] = db.get_clause_findings("DOC-1")
    [scope_item] = db.get_scope_items("DOC-1")
    [obligation] = db.get_obligations("DOC-1")
    [issue] = db.get_negotiation_issues("DOC-1")

    assert finding["topic"] == "Payment term"
    assert finding["risk_summary"] == "Long payment term"
    assert finding["requires_legal"] == 1
    assert scope_item["requirement_text"] == "Supply commissioning services"
    assert scope_item["included_in_quote"] == 1
    assert obligation["party"] == "Supplier"
    assert obligation["obligation_type"] == "PAY"
    assert obligation["trigger"] == "calendar"
    assert issue["issue"] == "Payment timing"
    assert issue["primary_ask"] == "Net 30"
    assert PROVENANCE_COLUMNS <= finding.keys()
    assert PROVENANCE_COLUMNS <= scope_item.keys()
    assert PROVENANCE_COLUMNS <= obligation.keys()
    assert PROVENANCE_COLUMNS <= issue.keys()
