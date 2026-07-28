"""
SQLite database layer for ContractIQ.
Full relational model: documents, clause_findings, scope_items,
obligations, negotiation_issues, report_packages.

On startup: if the legacy 'contracts' table exists, rows are
migrated to 'documents' automatically and silently.
"""

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from core.enums import ObligationType, TriggerType
from core.schemas import Provenance
from core.taxonomy import normalize_obligation_type, normalize_trigger


logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(exist_ok=True)
        self.db_path = str(db_path)
        self._init_schema()
        self._migrate_legacy()
        self._evolve_schema()
        self._evolve_provenance_schema()

    # ── Connection ────────────────────────────────────────────────────────────

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    filename TEXT NOT NULL,
                    file_path TEXT,
                    status TEXT DEFAULT 'uploaded',
                    upload_date TEXT,
                    analysis_date TEXT,
                    word_count INTEGER DEFAULT 0,
                    page_count INTEGER DEFAULT 0,
                    doc_type TEXT DEFAULT 'General Contract',
                    doc_type_confidence TEXT DEFAULT 'Low',
                    risk_score REAL DEFAULT 0,
                    risk_level TEXT DEFAULT 'Unknown',
                    executive_summary TEXT,
                    key_subject TEXT,
                    contract_value TEXT,
                    contract_duration TEXT,
                    governing_law TEXT,
                    counterparty TEXT,
                    analysis_json TEXT,
                    pdf_report_path TEXT,
                    excel_report_path TEXT,
                    raw_text TEXT,
                    error_message TEXT,
                    notes TEXT
                );

                CREATE TABLE IF NOT EXISTS clause_findings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL,
                    pillar TEXT NOT NULL,
                    topic TEXT,
                    clause_heading TEXT,
                    source_excerpt TEXT,
                    risk_summary TEXT,
                    severity TEXT,
                    confidence TEXT,
                    position TEXT,
                    fallback TEXT,
                    owner TEXT,
                    requires_legal INTEGER DEFAULT 0,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                );

                CREATE TABLE IF NOT EXISTS scope_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL,
                    project_id TEXT,
                    requirement_source TEXT,
                    requirement_text TEXT,
                    included_in_quote INTEGER DEFAULT 0,
                    excluded_in_quote INTEGER DEFAULT 0,
                    priced INTEGER DEFAULT 0,
                    owner TEXT,
                    gap_status TEXT,
                    comments TEXT,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                );

                CREATE TABLE IF NOT EXISTS obligations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL,
                    party TEXT,
                    obligation_type TEXT,
                    description TEXT,
                    trigger TEXT,
                    deadline TEXT,
                    notice_required TEXT,
                    owner TEXT,
                    status TEXT DEFAULT 'Open',
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                );

                CREATE TABLE IF NOT EXISTS negotiation_issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL,
                    pillar TEXT,
                    issue TEXT,
                    clause_reference TEXT,
                    source_excerpt TEXT,
                    risk_description TEXT,
                    severity TEXT,
                    primary_ask TEXT,
                    fallback TEXT,
                    counterparty_position TEXT,
                    response_strategy TEXT,
                    internal_owner TEXT,
                    requires_legal INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'Open',
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                );

                CREATE TABLE IF NOT EXISTS report_packages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL,
                    pdf_report_path TEXT,
                    excel_report_path TEXT,
                    created_date TEXT,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                );

                -- ── Knowledge & Rules Layer ──────────────────────────────────

                CREATE TABLE IF NOT EXISTS company_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    clause_type TEXT NOT NULL,
                    pillar TEXT,
                    position_summary TEXT NOT NULL,
                    acceptable_deviation TEXT,
                    hard_limit TEXT,
                    priority INTEGER DEFAULT 5,
                    active INTEGER DEFAULT 1,
                    created_date TEXT,
                    notes TEXT
                );

                CREATE TABLE IF NOT EXISTS insurance_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    insurance_type TEXT NOT NULL,
                    minimum_coverage_text TEXT,
                    minimum_coverage_amount REAL,
                    preferred_coverage_amount REAL,
                    required INTEGER DEFAULT 1,
                    notes TEXT,
                    active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS escalation_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_name TEXT NOT NULL,
                    pillar TEXT,
                    trigger_keywords TEXT,
                    trigger_condition TEXT,
                    minimum_severity TEXT DEFAULT 'High',
                    requires_legal INTEGER DEFAULT 0,
                    requires_exec INTEGER DEFAULT 0,
                    escalation_note TEXT,
                    priority INTEGER DEFAULT 5,
                    active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS product_risk_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_family TEXT NOT NULL,
                    risk_category TEXT,
                    typical_concern TEXT,
                    recommended_clause_language TEXT,
                    pillar TEXT,
                    notes TEXT,
                    active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS commercial_term_library (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    term_name TEXT NOT NULL,
                    term_category TEXT,
                    pillar TEXT,
                    our_standard TEXT,
                    minimum_acceptable TEXT,
                    never_accept TEXT,
                    notes TEXT,
                    active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS product_term_risk_map (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_family TEXT NOT NULL,
                    term_name TEXT NOT NULL,
                    concern_level TEXT DEFAULT 'Medium',
                    specific_concern TEXT,
                    recommended_action TEXT,
                    active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS deliverable_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_type TEXT NOT NULL,
                    deliverable_name TEXT NOT NULL,
                    description TEXT,
                    typical_acceptance_criteria TEXT,
                    notes TEXT,
                    active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS clause_playbooks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playbook_name TEXT NOT NULL,
                    pillar TEXT,
                    clause_type TEXT,
                    trigger_pattern TEXT,
                    situation_description TEXT,
                    recommended_response TEXT,
                    fallback_position TEXT,
                    escalate INTEGER DEFAULT 0,
                    notes TEXT,
                    active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS review_routing_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_name TEXT NOT NULL,
                    condition_description TEXT,
                    trigger_keywords TEXT,
                    trigger_pillar TEXT,
                    trigger_severity TEXT,
                    route_to TEXT NOT NULL,
                    routing_note TEXT,
                    priority INTEGER DEFAULT 5,
                    active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS negotiation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    counterparty TEXT,
                    clause_type TEXT,
                    pillar TEXT,
                    our_position TEXT,
                    their_position TEXT,
                    outcome TEXT,
                    settlement_language TEXT,
                    project_reference TEXT,
                    date_recorded TEXT,
                    notes TEXT
                );

                CREATE TABLE IF NOT EXISTS supplier_intelligence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    counterparty TEXT NOT NULL,
                    intel_type TEXT,
                    clause_type TEXT,
                    their_standard_position TEXT,
                    flexibility_observed TEXT,
                    general_intel TEXT,
                    source TEXT,
                    date_recorded TEXT,
                    active INTEGER DEFAULT 1,
                    notes TEXT
                );

                CREATE TABLE IF NOT EXISTS project_type_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_type TEXT NOT NULL,
                    typical_risks TEXT,
                    key_clauses_to_watch TEXT,
                    recommended_pillars TEXT,
                    delivery_model TEXT,
                    notes TEXT,
                    active INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS jurisdiction_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    jurisdiction TEXT NOT NULL,
                    rule_category TEXT,
                    rule_name TEXT NOT NULL,
                    rule_description TEXT,
                    impact_on_contract TEXT,
                    pillar TEXT,
                    requires_legal INTEGER DEFAULT 0,
                    active INTEGER DEFAULT 1,
                    notes TEXT
                );
            """)
            conn.commit()

    # ── Schema evolution (ALTER TABLE for new columns) ───────────────────────

    def _evolve_schema(self):
        """Add columns introduced after the initial release. Silent on duplicates."""
        docs_cols = [
            ("structured_markdown",      "TEXT"),
            ("contractual_items_json",   "TEXT"),
            ("tracker_path",             "TEXT"),
            ("business_role",            "TEXT"),
            ("delivery_model",           "TEXT"),
            ("product_families_json",    "TEXT"),
            ("jurisdiction",             "TEXT"),
            ("review_notes",             "TEXT"),
            ("review_priority",          "TEXT"),
            ("critical_flag_count",      "INTEGER"),
            ("high_flag_count",          "INTEGER"),
            ("negotiation_points_count", "INTEGER"),
        ]
        ni_cols = [
            ("proposed_response", "TEXT"),
        ]
        ob_cols = [
            ("notes", "TEXT"),
        ]
        with self._conn() as conn:
            for col, col_type in docs_cols:
                try:
                    conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {col_type}")
                except Exception:
                    pass
            for col, col_type in ni_cols:
                try:
                    conn.execute(f"ALTER TABLE negotiation_issues ADD COLUMN {col} {col_type}")
                except Exception:
                    pass
            for col, col_type in ob_cols:
                try:
                    conn.execute(f"ALTER TABLE obligations ADD COLUMN {col} {col_type}")
                except Exception:
                    pass
            conn.commit()

    def _evolve_provenance_schema(self) -> None:
        """Add and backfill flat provenance columns on analysis tables."""
        analysis_tables = (
            "clause_findings",
            "scope_items",
            "obligations",
            "negotiation_issues",
        )
        provenance_cols = (
            ("prov_created_by", "TEXT"),
            ("prov_agent_name", "TEXT"),
            ("prov_model", "TEXT"),
            ("prov_source_location", "TEXT"),
            ("prov_created_at", "TEXT"),
            ("human_confirmed", "INTEGER DEFAULT 0"),
            ("confirmed_by", "TEXT"),
            ("confirmed_at", "TEXT"),
        )
        migration_timestamp = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            for table in analysis_tables:
                existing_columns = {
                    row["name"]
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for col, col_type in provenance_cols:
                    if col in existing_columns:
                        continue
                    try:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                    except Exception:
                        pass
                conn.execute(
                    f"""
                    UPDATE {table}
                    SET prov_created_by = ?,
                        prov_agent_name = ?,
                        prov_model = NULL,
                        prov_created_at = ?,
                        human_confirmed = 0
                    WHERE prov_created_by IS NULL
                    """,
                    ("ai", "legacy_import", migration_timestamp),
                )
            conn.commit()

    # ── Legacy migration ──────────────────────────────────────────────────────

    def _migrate_legacy(self):
        """Copy rows from legacy 'contracts' table into 'documents', then rename."""
        with self._conn() as conn:
            tables = {
                r[0] for r in
                conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "contracts" not in tables or "contracts_legacy" in tables:
                return  # nothing to migrate

            # Columns present in both tables
            doc_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()
            }
            old_cols = {
                r[1] for r in conn.execute("PRAGMA table_info(contracts)").fetchall()
            }
            shared = doc_cols & old_cols

            # Map old column names to new ones
            col_remap = {
                "report_path": "pdf_report_path",  # rename
                "analysis_json": "analysis_json",
            }

            # Build column lists for INSERT … SELECT
            old_col_list = list(shared)
            new_col_list = [col_remap.get(c, c) for c in old_col_list if c in doc_cols]
            old_col_list = [c for c in old_col_list if c in doc_cols]

            # Also handle report_path → pdf_report_path
            if "report_path" in old_cols and "pdf_report_path" not in old_col_list:
                old_col_list.append("report_path")
                new_col_list.append("pdf_report_path")

            old_sel = ", ".join(old_col_list)
            new_ins = ", ".join(new_col_list)

            try:
                conn.execute(
                    f"INSERT OR IGNORE INTO documents ({new_ins}) "
                    f"SELECT {old_sel} FROM contracts"
                )
                conn.execute(
                    "ALTER TABLE contracts RENAME TO contracts_legacy"
                )
                conn.commit()
            except Exception:
                pass  # silent — never break startup

    # ── Documents CRUD ────────────────────────────────────────────────────────

    def create_document(self, data: Dict[str, Any]):
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO documents ({cols}) VALUES ({placeholders})",
                list(data.values())
            )
            conn.commit()

    # Legacy alias used by existing app.py code
    def create_contract(self, data: Dict[str, Any]):
        self.create_document(data)

    def get_document(self, doc_id: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
        if row:
            d = dict(row)
            if d.get("analysis_json"):
                try:
                    d["analysis"] = json.loads(d["analysis_json"])
                except Exception:
                    d["analysis"] = {}
            return d
        return None

    # Legacy alias
    def get_contract(self, contract_id: str) -> Optional[Dict]:
        return self.get_document(contract_id)

    def get_all_documents(self) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, filename, status, upload_date, analysis_date, "
                "word_count, page_count, doc_type, doc_type_confidence, "
                "risk_score, risk_level, executive_summary, "
                "pdf_report_path, excel_report_path, tracker_path, "
                "counterparty, contract_value, project_id, "
                "review_priority, critical_flag_count, high_flag_count, "
                "negotiation_points_count, "
                "CASE WHEN structured_markdown IS NOT NULL "
                "     AND LENGTH(structured_markdown) > 100 "
                "     THEN 1 ELSE 0 END as has_markdown, "
                "CASE WHEN structured_markdown IS NOT NULL "
                "     THEN LENGTH(structured_markdown) "
                "     ELSE 0 END as markdown_length "
                "FROM documents ORDER BY upload_date DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # Legacy alias
    def get_all_contracts(self) -> List[Dict]:
        return self.get_all_documents()

    def update_document(self, doc_id: str, updates: Dict[str, Any]):
        if not updates:
            return
        # Map legacy field names
        if "report_path" in updates:
            updates["pdf_report_path"] = updates.pop("report_path")
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [doc_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE documents SET {set_clause} WHERE id = ?", values
            )
            conn.commit()

    # Legacy alias
    def update_contract(self, contract_id: str, updates: Dict[str, Any]):
        self.update_document(contract_id, updates)

    def delete_document(self, doc_id: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM clause_findings WHERE document_id = ?", (doc_id,))
            conn.execute("DELETE FROM scope_items WHERE document_id = ?", (doc_id,))
            conn.execute("DELETE FROM obligations WHERE document_id = ?", (doc_id,))
            conn.execute("DELETE FROM negotiation_issues WHERE document_id = ?", (doc_id,))
            conn.execute("DELETE FROM report_packages WHERE document_id = ?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            conn.commit()

    # Legacy alias
    def delete_contract(self, contract_id: str):
        self.delete_document(contract_id)

    def get_documents_by_status(self, status: str) -> List[Dict]:
        """Return all documents with the given status (id, filename, status only)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, filename, status FROM documents WHERE status = ?",
                (status,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Clause Findings ───────────────────────────────────────────────────────

    @staticmethod
    def _provenance_values(provenance: Provenance) -> tuple[object, ...]:
        """Flatten provenance while reserving confirmation for confirm methods."""
        return (
            provenance.created_by.value,
            provenance.agent_name,
            provenance.model,
            provenance.source_location,
            provenance.created_at.isoformat(),
            0,
            None,
            None,
        )

    @staticmethod
    def _default_analysis_provenance(doc_id: str) -> Provenance:
        return Provenance.from_ai(
            agent_name="analysis_engine",
            model="unknown",
            source_document_id=doc_id,
        )

    def save_clause_findings(
        self,
        doc_id: str,
        pillar_results: List[Dict],
        provenance: Provenance | None = None,
    ) -> None:
        """Persist per-finding rows from pillar analysis results."""
        stamp = self._provenance_values(
            provenance or self._default_analysis_provenance(doc_id)
        )
        rows = []
        for pillar_data in pillar_results:
            pillar_id = pillar_data.get("pillar_id", "")
            for f in pillar_data.get("findings", []):
                rows.append((
                    doc_id, pillar_id,
                    f.get("finding"), f.get("clause_reference"),
                    f.get("source_excerpt"), f.get("detail"),
                    f.get("severity"), "Medium",
                    f.get("recommended_action"), None, None,
                    1 if f.get("requires_legal") else 0,
                    *stamp,
                ))
        if not rows:
            return
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO clause_findings "
                "(document_id, pillar, topic, clause_heading, source_excerpt, "
                " risk_summary, severity, confidence, position, fallback, owner, "
                " requires_legal, prov_created_by, prov_agent_name, prov_model, "
                " prov_source_location, prov_created_at, human_confirmed, "
                " confirmed_by, confirmed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows
            )
            conn.commit()

    def get_clause_findings(self, doc_id: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM clause_findings WHERE document_id = ? ORDER BY pillar, id",
                (doc_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Obligations ───────────────────────────────────────────────────────────

    def save_obligations(
        self,
        doc_id: str,
        obligations: List[Dict],
        provenance: Provenance | None = None,
    ) -> None:
        if not obligations:
            return
        stamp = self._provenance_values(
            provenance or self._default_analysis_provenance(doc_id)
        )
        rows = []
        for ob in obligations:
            raw_obligation_type = ob.get("obligation_type")
            raw_trigger = ob.get("trigger")
            obligation_type = normalize_obligation_type(raw_obligation_type)
            trigger = normalize_trigger(raw_trigger)
            if (
                raw_obligation_type is not None
                and obligation_type == raw_obligation_type
                and raw_obligation_type not in {item.value for item in ObligationType}
            ):
                logger.warning(
                    "Unrecognized obligation_type preserved unchanged: %r",
                    raw_obligation_type,
                )
            if (
                raw_trigger is not None
                and trigger == raw_trigger
                and raw_trigger not in {item.value for item in TriggerType}
            ):
                logger.warning("Unrecognized trigger preserved unchanged: %r", raw_trigger)
            rows.append((
                doc_id,
                ob.get("party"), obligation_type,
                ob.get("description"), trigger,
                ob.get("deadline"), ob.get("notice_required"),
                ob.get("owner"), ob.get("status", "Open"),
                *stamp,
            ))
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO obligations "
                "(document_id, party, obligation_type, description, trigger, "
                " deadline, notice_required, owner, status, prov_created_by, "
                " prov_agent_name, prov_model, prov_source_location, prov_created_at, "
                " human_confirmed, confirmed_by, confirmed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows
            )
            conn.commit()

    def get_obligations(self, doc_id: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM obligations WHERE document_id = ? ORDER BY party, id",
                (doc_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def update_obligation(self, ob_id: int, updates: Dict[str, Any]):
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [ob_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE obligations SET {set_clause} WHERE id = ?", values
            )
            conn.commit()

    # ── Negotiation Issues ────────────────────────────────────────────────────

    def save_negotiation_issues(
        self,
        doc_id: str,
        pillar_results: List[Dict],
        provenance: Provenance | None = None,
    ) -> None:
        """Flatten negotiation_points from all pillars into DB rows."""
        stamp = self._provenance_values(
            provenance or self._default_analysis_provenance(doc_id)
        )
        rows = []
        for pillar_data in pillar_results:
            pillar_id = pillar_data.get("pillar_id", "")
            for np in pillar_data.get("negotiation_points", []):
                rows.append((
                    doc_id, pillar_id,
                    np.get("issue"), None,
                    None, None,
                    np.get("priority"), np.get("primary_ask"),
                    np.get("fallback"), None, None, None,
                    1 if np.get("requires_legal") else 0,
                    "Open",
                    *stamp,
                ))
        if not rows:
            return
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO negotiation_issues "
                "(document_id, pillar, issue, clause_reference, source_excerpt, "
                " risk_description, severity, primary_ask, fallback, "
                " counterparty_position, response_strategy, internal_owner, "
                " requires_legal, status, prov_created_by, prov_agent_name, "
                " prov_model, prov_source_location, prov_created_at, "
                " human_confirmed, confirmed_by, confirmed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows
            )
            conn.commit()

    def get_negotiation_issues(self, doc_id: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM negotiation_issues WHERE document_id = ? ORDER BY pillar, severity, id",
                (doc_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_issues_for_document(self, doc_id: str) -> List[Dict]:
        """All negotiation issues ordered by severity (Critical first) then id."""
        sev_order = "CASE severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 WHEN 'Low' THEN 3 ELSE 4 END"
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM negotiation_issues WHERE document_id = ? ORDER BY {sev_order}, id",
                (doc_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_obligations_for_document(self, doc_id: str) -> List[Dict]:
        """All obligations ordered by deadline ASC (nulls last) then id."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM obligations WHERE document_id = ? "
                "ORDER BY CASE WHEN deadline IS NULL OR deadline = '' THEN 1 ELSE 0 END, deadline, id",
                (doc_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    _ISSUE_FIELDS = frozenset({
        "proposed_response", "internal_owner", "requires_legal",
        "status", "counterparty_position", "response_strategy",
    })
    _OBLIGATION_FIELDS = frozenset({"owner", "status", "deadline", "notes"})

    def update_issue(self, issue_id: int, field: str, value) -> bool:
        """Update a single validated field on a negotiation_issue row."""
        if field not in self._ISSUE_FIELDS:
            return False
        with self._conn() as conn:
            conn.execute(
                f"UPDATE negotiation_issues SET {field} = ? WHERE id = ?",
                (value, issue_id)
            )
            conn.commit()
        return True

    def update_obligation_field(self, ob_id: int, field: str, value) -> bool:
        """Update a single validated field on an obligation row."""
        if field not in self._OBLIGATION_FIELDS:
            return False
        with self._conn() as conn:
            conn.execute(
                f"UPDATE obligations SET {field} = ? WHERE id = ?",
                (value, ob_id)
            )
            conn.commit()
        return True

    def update_negotiation_issue(self, issue_id: int, updates: Dict[str, Any]):
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [issue_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE negotiation_issues SET {set_clause} WHERE id = ?", values
            )
            conn.commit()

    # ── Scope Items ───────────────────────────────────────────────────────────

    def save_scope_items(
        self,
        doc_id: str,
        items: List[Dict],
        provenance: Provenance | None = None,
    ) -> None:
        if not items:
            return
        stamp = self._provenance_values(
            provenance or self._default_analysis_provenance(doc_id)
        )
        rows = []
        for item in items:
            rows.append((
                doc_id, item.get("project_id"),
                item.get("requirement_source"), item.get("requirement_text"),
                1 if item.get("included_in_quote") else 0,
                1 if item.get("excluded_in_quote") else 0,
                1 if item.get("priced") else 0,
                item.get("owner"), item.get("gap_status"),
                item.get("comments"),
                *stamp,
            ))
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO scope_items "
                "(document_id, project_id, requirement_source, requirement_text, "
                " included_in_quote, excluded_in_quote, priced, owner, gap_status, comments, "
                " prov_created_by, prov_agent_name, prov_model, prov_source_location, "
                " prov_created_at, human_confirmed, confirmed_by, confirmed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows
            )
            conn.commit()

    def get_scope_items(self, doc_id: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM scope_items WHERE document_id = ? ORDER BY id",
                (doc_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def _confirm_analysis_row(
        self,
        table: str,
        row_id: int,
        confirmed_by: str,
    ) -> bool:
        with self._conn() as conn:
            cursor = conn.execute(
                f"""
                UPDATE {table}
                SET human_confirmed = 1,
                    confirmed_by = ?,
                    confirmed_at = ?
                WHERE id = ?
                """,
                (confirmed_by, datetime.now(UTC).isoformat(), row_id),
            )
            conn.commit()
        return cursor.rowcount > 0

    def confirm_clause_finding(self, finding_id: int, confirmed_by: str) -> bool:
        return self._confirm_analysis_row("clause_findings", finding_id, confirmed_by)

    def confirm_scope_item(self, item_id: int, confirmed_by: str) -> bool:
        return self._confirm_analysis_row("scope_items", item_id, confirmed_by)

    def confirm_obligation(self, ob_id: int, confirmed_by: str) -> bool:
        return self._confirm_analysis_row("obligations", ob_id, confirmed_by)

    def confirm_negotiation_issue(self, issue_id: int, confirmed_by: str) -> bool:
        return self._confirm_analysis_row("negotiation_issues", issue_id, confirmed_by)

    def count_unconfirmed(self, doc_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._conn() as conn:
            for table in (
                "clause_findings",
                "scope_items",
                "obligations",
                "negotiation_issues",
            ):
                row = conn.execute(
                    f"""
                    SELECT COUNT(*) AS count
                    FROM {table}
                    WHERE document_id = ? AND human_confirmed = 0
                    """,
                    (doc_id,),
                ).fetchone()
                counts[table] = int(row["count"])
        return counts

    # ── Report Packages ───────────────────────────────────────────────────────

    def save_report_package(self, doc_id: str, pdf_path: str,
                            excel_path: str, created_date: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO report_packages "
                "(document_id, pdf_report_path, excel_report_path, created_date) "
                "VALUES (?,?,?,?)",
                (doc_id, pdf_path, excel_path, created_date)
            )
            conn.commit()

    def get_report_packages(self, doc_id: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM report_packages WHERE document_id = ? ORDER BY id DESC",
                (doc_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Document Context ─────────────────────────────────────────────────────

    def update_document_context(self, doc_id: str, context: Dict[str, Any]):
        """Update review context fields on a document."""
        allowed = {"business_role", "delivery_model", "product_families_json",
                   "jurisdiction", "review_notes"}
        updates = {k: v for k, v in context.items() if k in allowed}
        if updates:
            self.update_document(doc_id, updates)

    # ── Knowledge Layer — generic helpers ────────────────────────────────────

    def _kget_all(self, table: str, active_only: bool = True) -> List[Dict]:
        where = "WHERE active = 1" if active_only else ""
        with self._conn() as conn:
            rows = conn.execute(f"SELECT * FROM {table} {where} ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def _kget_by_id(self, table: str, row_id: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
        return dict(row) if row else None

    def _kcreate(self, table: str, data: Dict[str, Any]) -> int:
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        with self._conn() as conn:
            cur = conn.execute(
                f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
                list(data.values())
            )
            conn.commit()
            return cur.lastrowid

    def _kupdate(self, table: str, row_id: int, updates: Dict[str, Any]):
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [row_id]
        with self._conn() as conn:
            conn.execute(f"UPDATE {table} SET {set_clause} WHERE id = ?", values)
            conn.commit()

    def _kdeactivate(self, table: str, row_id: int):
        with self._conn() as conn:
            conn.execute(f"UPDATE {table} SET active = 0 WHERE id = ?", (row_id,))
            conn.commit()

    def _kdelete(self, table: str, row_id: int):
        with self._conn() as conn:
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
            conn.commit()

    # ── company_positions ────────────────────────────────────────────────────

    def get_all_company_positions(self, active_only=True): return self._kget_all("company_positions", active_only)
    def get_company_position(self, row_id): return self._kget_by_id("company_positions", row_id)
    def create_company_position(self, data): return self._kcreate("company_positions", data)
    def update_company_position(self, row_id, updates): self._kupdate("company_positions", row_id, updates)
    def deactivate_company_position(self, row_id): self._kdeactivate("company_positions", row_id)
    def delete_company_position(self, row_id): self._kdelete("company_positions", row_id)

    def get_positions_for_pillar(self, pillar: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM company_positions WHERE (pillar = ? OR pillar IS NULL) AND active = 1 ORDER BY priority, id",
                (pillar,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── insurance_positions ──────────────────────────────────────────────────

    def get_all_insurance_positions(self, active_only=True): return self._kget_all("insurance_positions", active_only)
    def get_insurance_position(self, row_id): return self._kget_by_id("insurance_positions", row_id)
    def create_insurance_position(self, data): return self._kcreate("insurance_positions", data)
    def update_insurance_position(self, row_id, updates): self._kupdate("insurance_positions", row_id, updates)
    def deactivate_insurance_position(self, row_id): self._kdeactivate("insurance_positions", row_id)
    def delete_insurance_position(self, row_id): self._kdelete("insurance_positions", row_id)

    # ── escalation_rules ─────────────────────────────────────────────────────

    def get_all_escalation_rules(self, active_only=True): return self._kget_all("escalation_rules", active_only)
    def get_escalation_rule(self, row_id): return self._kget_by_id("escalation_rules", row_id)
    def create_escalation_rule(self, data): return self._kcreate("escalation_rules", data)
    def update_escalation_rule(self, row_id, updates): self._kupdate("escalation_rules", row_id, updates)
    def deactivate_escalation_rule(self, row_id): self._kdeactivate("escalation_rules", row_id)
    def delete_escalation_rule(self, row_id): self._kdelete("escalation_rules", row_id)

    def get_escalation_rules_for_pillar(self, pillar: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM escalation_rules WHERE (pillar = ? OR pillar IS NULL) AND active = 1 ORDER BY priority, id",
                (pillar,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── product_risk_profiles ────────────────────────────────────────────────

    def get_all_product_risk_profiles(self, active_only=True): return self._kget_all("product_risk_profiles", active_only)
    def get_product_risk_profile(self, row_id): return self._kget_by_id("product_risk_profiles", row_id)
    def create_product_risk_profile(self, data): return self._kcreate("product_risk_profiles", data)
    def update_product_risk_profile(self, row_id, updates): self._kupdate("product_risk_profiles", row_id, updates)
    def deactivate_product_risk_profile(self, row_id): self._kdeactivate("product_risk_profiles", row_id)
    def delete_product_risk_profile(self, row_id): self._kdelete("product_risk_profiles", row_id)

    def get_profiles_for_product(self, product_family: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM product_risk_profiles WHERE product_family = ? AND active = 1 ORDER BY pillar, id",
                (product_family,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── commercial_term_library ──────────────────────────────────────────────

    def get_all_commercial_terms(self, active_only=True): return self._kget_all("commercial_term_library", active_only)
    def get_commercial_term(self, row_id): return self._kget_by_id("commercial_term_library", row_id)
    def create_commercial_term(self, data): return self._kcreate("commercial_term_library", data)
    def update_commercial_term(self, row_id, updates): self._kupdate("commercial_term_library", row_id, updates)
    def deactivate_commercial_term(self, row_id): self._kdeactivate("commercial_term_library", row_id)
    def delete_commercial_term(self, row_id): self._kdelete("commercial_term_library", row_id)

    def search_commercial_terms(self, term_name: str = None, pillar: str = None) -> List[Dict]:
        query = "SELECT * FROM commercial_term_library WHERE active = 1"
        params = []
        if term_name:
            query += " AND LOWER(term_name) LIKE ?"
            params.append(f"%{term_name.lower()}%")
        if pillar:
            query += " AND (pillar = ? OR pillar IS NULL)"
            params.append(pillar)
        query += " ORDER BY term_name"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ── product_term_risk_map ────────────────────────────────────────────────

    def get_all_product_term_maps(self, active_only=True): return self._kget_all("product_term_risk_map", active_only)
    def get_product_term_map(self, row_id): return self._kget_by_id("product_term_risk_map", row_id)
    def create_product_term_map(self, data): return self._kcreate("product_term_risk_map", data)
    def update_product_term_map(self, row_id, updates): self._kupdate("product_term_risk_map", row_id, updates)
    def deactivate_product_term_map(self, row_id): self._kdeactivate("product_term_risk_map", row_id)
    def delete_product_term_map(self, row_id): self._kdelete("product_term_risk_map", row_id)

    def get_term_risks_for_product(self, product_family: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM product_term_risk_map WHERE product_family = ? AND active = 1 ORDER BY concern_level, id",
                (product_family,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── deliverable_templates ────────────────────────────────────────────────

    def get_all_deliverable_templates(self, active_only=True): return self._kget_all("deliverable_templates", active_only)
    def get_deliverable_template(self, row_id): return self._kget_by_id("deliverable_templates", row_id)
    def create_deliverable_template(self, data): return self._kcreate("deliverable_templates", data)
    def update_deliverable_template(self, row_id, updates): self._kupdate("deliverable_templates", row_id, updates)
    def deactivate_deliverable_template(self, row_id): self._kdeactivate("deliverable_templates", row_id)
    def delete_deliverable_template(self, row_id): self._kdelete("deliverable_templates", row_id)

    def get_templates_for_project_type(self, project_type: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM deliverable_templates WHERE project_type = ? AND active = 1 ORDER BY deliverable_name",
                (project_type,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── clause_playbooks ─────────────────────────────────────────────────────

    def get_all_clause_playbooks(self, active_only=True): return self._kget_all("clause_playbooks", active_only)
    def get_clause_playbook(self, row_id): return self._kget_by_id("clause_playbooks", row_id)
    def create_clause_playbook(self, data): return self._kcreate("clause_playbooks", data)
    def update_clause_playbook(self, row_id, updates): self._kupdate("clause_playbooks", row_id, updates)
    def deactivate_clause_playbook(self, row_id): self._kdeactivate("clause_playbooks", row_id)
    def delete_clause_playbook(self, row_id): self._kdelete("clause_playbooks", row_id)

    def get_playbooks_for_pillar(self, pillar: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM clause_playbooks WHERE (pillar = ? OR pillar IS NULL) AND active = 1 ORDER BY clause_type, id",
                (pillar,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── review_routing_rules ─────────────────────────────────────────────────

    def get_all_routing_rules(self, active_only=True): return self._kget_all("review_routing_rules", active_only)
    def get_routing_rule(self, row_id): return self._kget_by_id("review_routing_rules", row_id)
    def create_routing_rule(self, data): return self._kcreate("review_routing_rules", data)
    def update_routing_rule(self, row_id, updates): self._kupdate("review_routing_rules", row_id, updates)
    def deactivate_routing_rule(self, row_id): self._kdeactivate("review_routing_rules", row_id)
    def delete_routing_rule(self, row_id): self._kdelete("review_routing_rules", row_id)

    def get_routing_rules_for_severity(self, severity: str, pillar: str = None) -> List[Dict]:
        sev_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
        sev_val = sev_rank.get(severity, 99)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM review_routing_rules WHERE active = 1 ORDER BY priority, id"
            ).fetchall()
        results = []
        for r in rows:
            rd = dict(r)
            ts = rd.get("trigger_severity")
            tp = rd.get("trigger_pillar")
            if ts and sev_rank.get(ts, 99) < sev_val:
                continue  # rule requires higher severity than we have
            if pillar and tp and tp != pillar:
                continue
            results.append(rd)
        return results

    # ── negotiation_history ──────────────────────────────────────────────────

    def get_all_negotiation_history(self): return self._kget_all("negotiation_history", active_only=False)
    def get_negotiation_record(self, row_id): return self._kget_by_id("negotiation_history", row_id)
    def create_negotiation_record(self, data): return self._kcreate("negotiation_history", data)
    def update_negotiation_record(self, row_id, updates): self._kupdate("negotiation_history", row_id, updates)
    def delete_negotiation_record(self, row_id): self._kdelete("negotiation_history", row_id)

    def get_history_for_counterparty(self, counterparty: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM negotiation_history WHERE LOWER(counterparty) LIKE ? ORDER BY date_recorded DESC, id DESC",
                (f"%{counterparty.lower()}%",)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── supplier_intelligence ────────────────────────────────────────────────

    def get_all_supplier_intelligence(self, active_only=True): return self._kget_all("supplier_intelligence", active_only)
    def get_supplier_intel_record(self, row_id): return self._kget_by_id("supplier_intelligence", row_id)
    def create_supplier_intel(self, data): return self._kcreate("supplier_intelligence", data)
    def update_supplier_intel(self, row_id, updates): self._kupdate("supplier_intelligence", row_id, updates)
    def deactivate_supplier_intel(self, row_id): self._kdeactivate("supplier_intelligence", row_id)
    def delete_supplier_intel(self, row_id): self._kdelete("supplier_intelligence", row_id)

    def get_intel_for_counterparty(self, counterparty: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM supplier_intelligence WHERE LOWER(counterparty) LIKE ? AND active = 1 ORDER BY intel_type, id",
                (f"%{counterparty.lower()}%",)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── project_type_profiles ────────────────────────────────────────────────

    def get_all_project_type_profiles(self, active_only=True): return self._kget_all("project_type_profiles", active_only)
    def get_project_type_profile(self, row_id): return self._kget_by_id("project_type_profiles", row_id)
    def create_project_type_profile(self, data): return self._kcreate("project_type_profiles", data)
    def update_project_type_profile(self, row_id, updates): self._kupdate("project_type_profiles", row_id, updates)
    def deactivate_project_type_profile(self, row_id): self._kdeactivate("project_type_profiles", row_id)
    def delete_project_type_profile(self, row_id): self._kdelete("project_type_profiles", row_id)

    # ── jurisdiction_rules ───────────────────────────────────────────────────

    def get_all_jurisdiction_rules(self, active_only=True): return self._kget_all("jurisdiction_rules", active_only)
    def get_jurisdiction_rule(self, row_id): return self._kget_by_id("jurisdiction_rules", row_id)
    def create_jurisdiction_rule(self, data): return self._kcreate("jurisdiction_rules", data)
    def update_jurisdiction_rule(self, row_id, updates): self._kupdate("jurisdiction_rules", row_id, updates)
    def deactivate_jurisdiction_rule(self, row_id): self._kdeactivate("jurisdiction_rules", row_id)
    def delete_jurisdiction_rule(self, row_id): self._kdelete("jurisdiction_rules", row_id)

    def get_rules_for_jurisdiction(self, jurisdiction: str) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jurisdiction_rules WHERE LOWER(jurisdiction) LIKE ? AND active = 1 ORDER BY pillar, rule_category, id",
                (f"%{jurisdiction.lower()}%",)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Knowledge stats ───────────────────────────────────────────────────────

    def get_knowledge_counts(self) -> Dict[str, int]:
        """Row counts for all knowledge tables (active rows only where applicable)."""
        tables_active = [
            "company_positions", "insurance_positions", "escalation_rules",
            "product_risk_profiles", "commercial_term_library", "product_term_risk_map",
            "deliverable_templates", "clause_playbooks", "review_routing_rules",
            "supplier_intelligence", "project_type_profiles", "jurisdiction_rules",
        ]
        result = {}
        with self._conn() as conn:
            for t in tables_active:
                n = conn.execute(f"SELECT COUNT(*) FROM {t} WHERE active = 1").fetchone()[0]
                result[t] = n
            nh = conn.execute("SELECT COUNT(*) FROM negotiation_history").fetchone()[0]
            result["negotiation_history"] = nh
        return result

    # ── Stats ─────────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            complete = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE status = 'complete'"
            ).fetchone()[0]
            high_risk = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE risk_level IN ('High', 'Critical')"
            ).fetchone()[0]
        return {"total": total, "complete": complete, "high_risk": high_risk}
