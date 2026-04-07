"""
SQLite database layer for ContractIQ.
Full relational model: documents, clause_findings, scope_items,
obligations, negotiation_issues, report_packages.

On startup: if the legacy 'contracts' table exists, rows are
migrated to 'documents' automatically and silently.
"""

import sqlite3
import json
from pathlib import Path
from typing import Optional, List, Dict, Any


class Database:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(exist_ok=True)
        self.db_path = str(db_path)
        self._init_schema()
        self._migrate_legacy()
        self._evolve_schema()

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
            """)
            conn.commit()

    # ── Schema evolution (ALTER TABLE for new columns) ───────────────────────

    def _evolve_schema(self):
        """Add columns introduced after the initial release. Silent on duplicates."""
        new_cols = [
            ("structured_markdown",    "TEXT"),
            ("contractual_items_json", "TEXT"),
            ("tracker_path",           "TEXT"),
        ]
        with self._conn() as conn:
            for col, col_type in new_cols:
                try:
                    conn.execute(
                        f"ALTER TABLE documents ADD COLUMN {col} {col_type}"
                    )
                except Exception:
                    pass  # column already exists
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
                "counterparty, contract_value, project_id "
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

    # ── Clause Findings ───────────────────────────────────────────────────────

    def save_clause_findings(self, doc_id: str, pillar_results: List[Dict]):
        """Persist per-finding rows from pillar analysis results."""
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
                ))
        if not rows:
            return
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO clause_findings "
                "(document_id, pillar, topic, clause_heading, source_excerpt, "
                " risk_summary, severity, confidence, position, fallback, owner, "
                " requires_legal) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
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

    def save_obligations(self, doc_id: str, obligations: List[Dict]):
        if not obligations:
            return
        rows = []
        for ob in obligations:
            rows.append((
                doc_id,
                ob.get("party"), ob.get("obligation_type"),
                ob.get("description"), ob.get("trigger"),
                ob.get("deadline"), ob.get("notice_required"),
                ob.get("owner"), ob.get("status", "Open"),
            ))
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO obligations "
                "(document_id, party, obligation_type, description, trigger, "
                " deadline, notice_required, owner, status) VALUES (?,?,?,?,?,?,?,?,?)",
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

    def save_negotiation_issues(self, doc_id: str, pillar_results: List[Dict]):
        """Flatten negotiation_points from all pillars into DB rows."""
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
                ))
        if not rows:
            return
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO negotiation_issues "
                "(document_id, pillar, issue, clause_reference, source_excerpt, "
                " risk_description, severity, primary_ask, fallback, "
                " counterparty_position, response_strategy, internal_owner, "
                " requires_legal, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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

    def save_scope_items(self, doc_id: str, items: List[Dict]):
        if not items:
            return
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
            ))
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO scope_items "
                "(document_id, project_id, requirement_source, requirement_text, "
                " included_in_quote, excluded_in_quote, priced, owner, gap_status, comments) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
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
