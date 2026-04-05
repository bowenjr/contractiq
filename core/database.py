"""
SQLite database layer for ContractIQ.
Stores all contracts, analysis results, and metadata.
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

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS contracts (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    file_path TEXT,
                    status TEXT DEFAULT 'uploaded',
                    upload_date TEXT,
                    analysis_date TEXT,
                    word_count INTEGER DEFAULT 0,
                    page_count INTEGER DEFAULT 0,
                    doc_type TEXT DEFAULT 'Contract',
                    risk_score REAL DEFAULT 0,
                    risk_level TEXT DEFAULT 'Unknown',
                    executive_summary TEXT,
                    analysis_json TEXT,
                    report_path TEXT,
                    raw_text TEXT,
                    error_message TEXT,
                    notes TEXT
                )
            """)
            conn.commit()

    def create_contract(self, data: Dict[str, Any]):
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO contracts ({cols}) VALUES ({placeholders})",
                list(data.values())
            )
            conn.commit()

    def get_contract(self, contract_id: str) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM contracts WHERE id = ?", (contract_id,)
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

    def get_all_contracts(self) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, filename, status, upload_date, analysis_date, "
                "word_count, page_count, doc_type, risk_score, risk_level, "
                "executive_summary, report_path "
                "FROM contracts ORDER BY upload_date DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_contract(self, contract_id: str, updates: Dict[str, Any]):
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [contract_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE contracts SET {set_clause} WHERE id = ?", values
            )
            conn.commit()

    def delete_contract(self, contract_id: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM contracts WHERE id = ?", (contract_id,))
            conn.commit()

    def get_stats(self) -> Dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
            complete = conn.execute(
                "SELECT COUNT(*) FROM contracts WHERE status = 'complete'"
            ).fetchone()[0]
            high_risk = conn.execute(
                "SELECT COUNT(*) FROM contracts WHERE risk_level IN ('High', 'Critical')"
            ).fetchone()[0]
        return {"total": total, "complete": complete, "high_risk": high_risk}
