"""Persistence for ContractIQ's bid-centric deterministic spine."""

import json
import sqlite3
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import cast
from uuid import uuid4

from core.database import Database
from core.document_control import ControlledDocumentIdentityError
from core.enums import (
    ApprovalType,
    BidLevel,
    BidStatus,
    CustomerType,
    Gate,
    GateStatus,
    InferencePolicy,
    RiskTrigger,
)
from core.schemas import Approval, AuditEntry, Bid, GateRecord, Provenance

# SQL predicate selecting gate approvals that no later decision has superseded. Every
# reader of "the current gate fact" must use it so a historical NOT_OBTAINED row is never
# mistaken for the live decision.
CURRENT_APPROVAL_CLAUSE = (
    "NOT EXISTS (SELECT 1 FROM approvals _later "
    "WHERE _later.supersedes_approval_id = approvals.approval_id)"
)


class BidRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._evolve_bid_schema()

    def _conn(self) -> sqlite3.Connection:
        return cast(sqlite3.Connection, self.db._conn())

    def _evolve_bid_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bids (
                    bid_id TEXT PRIMARY KEY,
                    customer TEXT NOT NULL,
                    customer_type TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    location TEXT,
                    sales_owner TEXT NOT NULL,
                    bc_owner TEXT NOT NULL,
                    executive_sponsor TEXT,
                    release_date TEXT NOT NULL,
                    customer_due_date TEXT NOT NULL,
                    internal_due_date TEXT NOT NULL,
                    anticipated_award_date TEXT,
                    estimated_value TEXT NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'CAD',
                    margin_range TEXT,
                    win_probability INTEGER,
                    classification TEXT NOT NULL,
                    current_gate TEXT NOT NULL DEFAULT 'g0',
                    status TEXT NOT NULL DEFAULT 'active',
                    risk_triggers TEXT NOT NULL DEFAULT '[]',
                    inference_policy TEXT NOT NULL DEFAULT 'local_only',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    customer_due_time TEXT,
                    customer_due_timezone TEXT
                );

                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    bid_id TEXT NOT NULL,
                    approval_type TEXT NOT NULL,
                    required INTEGER NOT NULL DEFAULT 1,
                    obtained INTEGER NOT NULL DEFAULT 0,
                    authority TEXT,
                    evidence_ref TEXT,
                    decision TEXT,
                    decided_at TEXT,
                    provenance_json TEXT NOT NULL,
                    FOREIGN KEY (bid_id) REFERENCES bids(bid_id)
                );

                CREATE TABLE IF NOT EXISTS gate_records (
                    bid_id TEXT NOT NULL,
                    gate TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'not_started',
                    blockers TEXT NOT NULL DEFAULT '[]',
                    override_by TEXT,
                    override_risk_note TEXT,
                    decided_at TEXT,
                    PRIMARY KEY (bid_id, gate),
                    FOREIGN KEY (bid_id) REFERENCES bids(bid_id)
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    entry_id TEXT PRIMARY KEY,
                    bid_id TEXT,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_approvals_bid ON approvals(bid_id);
                CREATE INDEX IF NOT EXISTS idx_gate_records_bid ON gate_records(bid_id);
                CREATE INDEX IF NOT EXISTS idx_audit_bid ON audit_log(bid_id);
                """
            )
            document_columns = {
                str(row["name"]) for row in conn.execute("PRAGMA table_info(documents)").fetchall()
            }
            if "bid_id" not in document_columns:
                conn.execute("ALTER TABLE documents ADD COLUMN bid_id TEXT REFERENCES bids(bid_id)")
            bid_columns = {
                str(row["name"]) for row in conn.execute("PRAGMA table_info(bids)").fetchall()
            }
            for column in ("customer_due_time", "customer_due_timezone"):
                if column not in bid_columns:
                    conn.execute(f"ALTER TABLE bids ADD COLUMN {column} TEXT")
            self._evolve_approval_lineage(conn)
            conn.commit()

    @staticmethod
    def _evolve_approval_lineage(conn: sqlite3.Connection) -> None:
        """Additively give gate approvals an append-only supersession lineage.

        A recorded NOT_OBTAINED decision stays as historical evidence; a later OBTAINED
        decision supersedes it. A baseline-bound approval may likewise supersede approval
        evidence tied to an earlier selected baseline. Existing rows keep NULL lineage
        and baseline columns and are never rewritten.
        """
        columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(approvals)").fetchall()
        }
        if "supersedes_approval_id" not in columns:
            conn.execute(
                "ALTER TABLE approvals ADD COLUMN supersedes_approval_id TEXT "
                "REFERENCES approvals(approval_id)"
            )
        if "recorded_at" not in columns:
            conn.execute("ALTER TABLE approvals ADD COLUMN recorded_at TEXT")
        if "pricing_baseline_id" not in columns:
            conn.execute("ALTER TABLE approvals ADD COLUMN pricing_baseline_id TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_superseded_once "
            "ON approvals(supersedes_approval_id) WHERE supersedes_approval_id IS NOT NULL"
        )
        # Gate approvals are append-only evidence: no row is ever edited or deleted, whether
        # or not it has been superseded. (Earlier builds only protected superseded rows,
        # which left a current decision editable in place; drop those narrower triggers.)
        for legacy_trigger in ("approvals_superseded_immutable", "approvals_superseded_no_delete"):
            conn.execute(f"DROP TRIGGER IF EXISTS {legacy_trigger}")
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS approvals_append_only_update "
            "BEFORE UPDATE ON approvals "
            "BEGIN SELECT RAISE(ABORT,'gate approvals are append-only: a recorded decision "
            "is historical evidence; record a superseding decision instead'); END"
        )
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS approvals_append_only_delete "
            "BEFORE DELETE ON approvals "
            "BEGIN SELECT RAISE(ABORT,'gate approvals cannot be deleted'); END"
        )
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS approvals_single_current "
            "BEFORE INSERT ON approvals WHEN NEW.supersedes_approval_id IS NULL "
            "AND EXISTS(SELECT 1 FROM approvals o WHERE o.bid_id=NEW.bid_id "
            "AND o.approval_type=NEW.approval_type AND NOT EXISTS(SELECT 1 FROM approvals l "
            "WHERE l.supersedes_approval_id=o.approval_id)) "
            "BEGIN SELECT RAISE(ABORT,'a current decision already exists for this control; "
            "supersede it instead of adding a second one'); END"
        )
        conn.execute("DROP TRIGGER IF EXISTS approvals_supersession_scope")
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS approvals_supersession_scope "
            "BEFORE INSERT ON approvals WHEN NEW.supersedes_approval_id IS NOT NULL "
            "AND NOT EXISTS(SELECT 1 FROM approvals o "
            "WHERE o.approval_id=NEW.supersedes_approval_id "
            "AND o.bid_id=NEW.bid_id AND o.approval_type=NEW.approval_type "
            "AND (o.obtained=0 OR (NEW.obtained=1 "
            "AND o.pricing_baseline_id IS NOT NEW.pricing_baseline_id))) "
            "BEGIN SELECT RAISE(ABORT,'only a not-obtained or differently baseline-bound "
            "approval of the same Bid and control can be superseded'); END"
        )

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return None if value is None else str(value)

    @staticmethod
    def _bid_values(bid: Bid) -> tuple[object, ...]:
        return (
            bid.bid_id,
            bid.customer,
            bid.customer_type.value,
            bid.project_name,
            bid.location,
            bid.sales_owner,
            bid.bc_owner,
            bid.executive_sponsor,
            bid.release_date.isoformat(),
            bid.customer_due_date.isoformat(),
            bid.internal_due_date.isoformat(),
            bid.anticipated_award_date.isoformat() if bid.anticipated_award_date else None,
            str(bid.estimated_value),
            bid.currency,
            bid.margin_range,
            bid.win_probability,
            bid.classification.value,
            bid.current_gate.value,
            bid.status.value,
            json.dumps([trigger.value for trigger in bid.risk_triggers]),
            bid.inference_policy.value,
            bid.created_at.isoformat(),
            bid.updated_at.isoformat(),
            bid.customer_due_time.isoformat() if bid.customer_due_time else None,
            bid.customer_due_timezone,
        )

    @classmethod
    def _bid_from_row(cls, row: sqlite3.Row) -> Bid:
        risk_trigger_values = cast(list[str], json.loads(str(row["risk_triggers"])))
        anticipated_award = cls._optional_str(row["anticipated_award_date"])
        win_probability = row["win_probability"]
        return Bid(
            bid_id=str(row["bid_id"]),
            customer=str(row["customer"]),
            customer_type=CustomerType(str(row["customer_type"])),
            project_name=str(row["project_name"]),
            location=cls._optional_str(row["location"]),
            sales_owner=str(row["sales_owner"]),
            bc_owner=str(row["bc_owner"]),
            executive_sponsor=cls._optional_str(row["executive_sponsor"]),
            release_date=date.fromisoformat(str(row["release_date"])),
            customer_due_date=date.fromisoformat(str(row["customer_due_date"])),
            customer_due_time=(
                time.fromisoformat(str(row["customer_due_time"]))
                if row["customer_due_time"]
                else None
            ),
            customer_due_timezone=cls._optional_str(row["customer_due_timezone"]),
            internal_due_date=date.fromisoformat(str(row["internal_due_date"])),
            anticipated_award_date=(
                date.fromisoformat(anticipated_award) if anticipated_award is not None else None
            ),
            estimated_value=Decimal(str(row["estimated_value"])),
            currency=str(row["currency"]),
            margin_range=cls._optional_str(row["margin_range"]),
            win_probability=int(win_probability) if win_probability is not None else None,
            classification=BidLevel(str(row["classification"])),
            current_gate=Gate(str(row["current_gate"])),
            status=BidStatus(str(row["status"])),
            risk_triggers=[RiskTrigger(value) for value in risk_trigger_values],
            inference_policy=InferencePolicy(str(row["inference_policy"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @classmethod
    def _insert_bid(cls, conn: sqlite3.Connection, bid: Bid) -> None:
        conn.execute(
            """
            INSERT INTO bids (
                bid_id, customer, customer_type, project_name, location,
                sales_owner, bc_owner, executive_sponsor, release_date,
                customer_due_date, internal_due_date, anticipated_award_date,
                estimated_value, currency, margin_range, win_probability,
                classification, current_gate, status, risk_triggers,
                inference_policy, created_at, updated_at,
                customer_due_time, customer_due_timezone
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            cls._bid_values(bid),
        )

    def create_bid(self, bid: Bid) -> None:
        try:
            with self._conn() as conn:
                self._insert_bid(conn, bid)
                conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Bid already exists: {bid.bid_id}") from exc

    def create_bid_with_audit(self, bid: Bid, audit: AuditEntry) -> None:
        """Create a browser-authored bid and its audit evidence atomically."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._insert_bid(conn, bid)
                conn.execute(
                    "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        audit.entry_id,
                        audit.bid_id,
                        audit.actor,
                        audit.action,
                        audit.detail,
                        audit.timestamp.isoformat(),
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                if self.bid_exists(bid.bid_id):
                    raise ValueError(f"Bid already exists: {bid.bid_id}") from exc
                raise ValueError("Bid / Project creation could not be committed") from exc
            except Exception:
                conn.rollback()
                raise

    def get_bid(self, bid_id: str) -> Bid | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM bids WHERE bid_id = ?", (bid_id,)).fetchone()
        return self._bid_from_row(row) if row is not None else None

    def list_bids(self, status: BidStatus | None = None) -> list[Bid]:
        with self._conn() as conn:
            if status is None:
                rows = conn.execute(
                    "SELECT * FROM bids ORDER BY created_at DESC, bid_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bids WHERE status = ? ORDER BY created_at DESC, bid_id",
                    (status.value,),
                ).fetchall()
        return [self._bid_from_row(row) for row in rows]

    def update_bid(self, bid: Bid) -> None:
        updated_bid = bid.model_copy(update={"updated_at": datetime.now(UTC)})
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO bids (
                    bid_id, customer, customer_type, project_name, location,
                    sales_owner, bc_owner, executive_sponsor, release_date,
                    customer_due_date, internal_due_date, anticipated_award_date,
                    estimated_value, currency, margin_range, win_probability,
                    classification, current_gate, status, risk_triggers,
                    inference_policy, created_at, updated_at,
                    customer_due_time, customer_due_timezone
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(bid_id) DO UPDATE SET
                    customer = excluded.customer,
                    customer_type = excluded.customer_type,
                    project_name = excluded.project_name,
                    location = excluded.location,
                    sales_owner = excluded.sales_owner,
                    bc_owner = excluded.bc_owner,
                    executive_sponsor = excluded.executive_sponsor,
                    release_date = excluded.release_date,
                    customer_due_date = excluded.customer_due_date,
                    internal_due_date = excluded.internal_due_date,
                    anticipated_award_date = excluded.anticipated_award_date,
                    estimated_value = excluded.estimated_value,
                    currency = excluded.currency,
                    margin_range = excluded.margin_range,
                    win_probability = excluded.win_probability,
                    classification = excluded.classification,
                    current_gate = excluded.current_gate,
                    status = excluded.status,
                    risk_triggers = excluded.risk_triggers,
                    inference_policy = excluded.inference_policy,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    customer_due_time = excluded.customer_due_time,
                    customer_due_timezone = excluded.customer_due_timezone
                """,
                self._bid_values(updated_bid),
            )
            conn.commit()

    def record_customer_closing(
        self,
        bid_id: str,
        closing_time: time | None,
        timezone: str | None,
        actor: str,
        source_note: str,
    ) -> Bid:
        """Record (or clear) the customer's closing time and zone, with its audit row.

        The Bid is revalidated first, so an unknown zone or a time without a zone is refused with
        a message before anything is written. The change and its audit entry share one transaction.
        """
        bid = self.get_bid(bid_id)
        if bid is None:
            raise ValueError(f"Bid not found: {bid_id}")
        updated = Bid.model_validate(
            {
                **bid.model_dump(),
                "customer_due_time": closing_time,
                "customer_due_timezone": timezone,
                "updated_at": datetime.now(UTC),
            }
        )
        label = updated.customer_closing_label()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE bids SET customer_due_time=?, customer_due_timezone=?, updated_at=? "
                    "WHERE bid_id=?",
                    (
                        closing_time.isoformat() if closing_time else None,
                        timezone,
                        updated.updated_at.isoformat(),
                        bid_id,
                    ),
                )
                conn.execute(
                    "INSERT INTO audit_log(entry_id,bid_id,actor,action,detail,timestamp) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        f"AUD-{uuid4().hex}",
                        bid_id,
                        actor,
                        "customer_closing_recorded",
                        f"Customer closing recorded as {label}. Source: {source_note}",
                        updated.updated_at.isoformat(),
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return updated

    def bid_exists(self, bid_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM bids WHERE bid_id = ? LIMIT 1", (bid_id,)).fetchone()
        return row is not None

    @staticmethod
    def _controlled_document_row(conn: sqlite3.Connection, doc_id: str) -> bool:
        columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(documents)").fetchall()
        }
        if "control_managed" not in columns:
            return False
        row = conn.execute(
            "SELECT control_managed FROM documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
        return row is not None and int(row["control_managed"] or 0) == 1

    def attach_document_to_bid(self, doc_id: str, bid_id: str) -> None:
        with self._conn() as conn:
            if self._controlled_document_row(conn, doc_id):
                raise ControlledDocumentIdentityError(
                    "controlled document bid ownership cannot be reassigned"
                )
            conn.execute("UPDATE documents SET bid_id = ? WHERE id = ?", (bid_id, doc_id))
            conn.commit()

    def detach_document(self, doc_id: str) -> None:
        with self._conn() as conn:
            if self._controlled_document_row(conn, doc_id):
                raise ControlledDocumentIdentityError(
                    "controlled document bid ownership cannot be detached"
                )
            conn.execute("UPDATE documents SET bid_id = NULL WHERE id = ?", (doc_id,))
            conn.commit()

    def list_documents_for_bid(self, bid_id: str) -> list[dict[str, object]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM documents WHERE bid_id = ? ORDER BY upload_date DESC, id",
                (bid_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> Approval:
        decided_at = BidRepository._optional_str(row["decided_at"])
        return Approval(
            approval_id=str(row["approval_id"]),
            bid_id=str(row["bid_id"]),
            approval_type=ApprovalType(str(row["approval_type"])),
            required=bool(row["required"]),
            obtained=bool(row["obtained"]),
            authority=BidRepository._optional_str(row["authority"]),
            evidence_ref=BidRepository._optional_str(row["evidence_ref"]),
            decision=BidRepository._optional_str(row["decision"]),
            decided_at=datetime.fromisoformat(decided_at) if decided_at is not None else None,
            provenance=Provenance.model_validate_json(str(row["provenance_json"])),
            pricing_baseline_id=BidRepository._optional_str(row["pricing_baseline_id"]),
            supersedes_approval_id=BidRepository._optional_str(row["supersedes_approval_id"]),
            recorded_at=(
                datetime.fromisoformat(str(row["recorded_at"]))
                if row["recorded_at"] is not None
                else None
            ),
        )

    def create_approval(self, approval: Approval, actor: str | None = None) -> None:
        """Create the first gate approval for a control (later decisions are appended)."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM approvals WHERE bid_id=? AND approval_type=?",
                (approval.bid_id, approval.approval_type.value),
            ).fetchone():
                raise ValueError(
                    "This Bid gate approval has already been recorded; use "
                    "record_gate_approval to supersede it"
                )
            conn.execute(
                """
                INSERT INTO approvals (
                    approval_id, bid_id, approval_type, required, obtained,
                    authority, evidence_ref, decision, decided_at, provenance_json,
                    recorded_at, pricing_baseline_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    approval.approval_id,
                    approval.bid_id,
                    approval.approval_type.value,
                    int(approval.required),
                    int(approval.obtained),
                    approval.authority,
                    approval.evidence_ref,
                    approval.decision,
                    approval.decided_at.isoformat() if approval.decided_at else None,
                    approval.provenance.model_dump_json(),
                    approval.recorded_at.isoformat() if approval.recorded_at else None,
                    approval.pricing_baseline_id,
                ),
            )
            if actor is not None:
                at = datetime.now(UTC)
                conn.execute(
                    """INSERT INTO audit_log(
                    entry_id,bid_id,actor,action,detail,timestamp
                    ) VALUES(?,?,?,?,?,?)""",
                    (
                        f"AUD-{uuid4()}",
                        approval.bid_id,
                        actor,
                        "bid_gate_approval_created",
                        json.dumps(
                            {
                                "approval_id": approval.approval_id,
                                "gate": approval.approval_type.value,
                                "decision": approval.decision,
                                "obtained": approval.obtained,
                            },
                            sort_keys=True,
                        ),
                        at.isoformat(),
                    ),
                )
            conn.commit()

    def list_approvals(self, bid_id: str) -> list[Approval]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE bid_id = ? ORDER BY approval_id",
                (bid_id,),
            ).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def list_current_approvals(self, bid_id: str) -> list[Approval]:
        """Return each gate approval that has not been superseded by a later decision.

        This is the set the gates and downstream readiness consume. A superseded
        NOT_OBTAINED decision remains in :meth:`list_approvals` as history only.
        """
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM approvals WHERE bid_id = ? AND {CURRENT_APPROVAL_CLAUSE} "
                "ORDER BY approval_id",
                (bid_id,),
            ).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def record_gate_approval(
        self,
        approval: Approval,
        actor: str,
        expected_current_approval_id: str | None = None,
    ) -> str:
        """Record a browser-authored gate decision append-only.

        Returns ``"created"`` for the first decision on a control, ``"superseded"`` when
        an OBTAINED decision follows a recorded NOT_OBTAINED one (which is kept as
        history), or ``"unchanged"`` when the identical decision is already current, so
        a repeated submission is idempotent and writes nothing.

        Raises:
            ValueError: If the decision conflicts with the current one, or if
                ``expected_current_approval_id`` no longer names the current decision
                (another user recorded it first; reload and retry), or if an obtained
                senior approval has no selected pricing baseline to bind.
        """
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                pricing_baseline_id: str | None = None
                if approval.obtained and approval.approval_type is not ApprovalType.BID_NO_BID:
                    baseline_table = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='scenario_baselines'"
                    ).fetchone()
                    if baseline_table is not None:
                        baseline = conn.execute(
                            "SELECT selection_id FROM scenario_baselines WHERE bid_id=? "
                            "ORDER BY selected_at DESC,selection_id DESC LIMIT 1",
                            (approval.bid_id,),
                        ).fetchone()
                        pricing_baseline_id = (
                            str(baseline["selection_id"]) if baseline is not None else None
                        )
                    if pricing_baseline_id is None:
                        raise ValueError(
                            "Select the pricing baseline before recording a senior approval"
                        )
                heads = conn.execute(
                    f"SELECT * FROM approvals WHERE bid_id=? AND approval_type=? "
                    f"AND {CURRENT_APPROVAL_CLAUSE} ORDER BY approval_id",
                    (approval.bid_id, approval.approval_type.value),
                ).fetchall()
                supersedes: str | None = None
                if len(heads) > 1:
                    raise ValueError(
                        "This control has more than one current legacy record; "
                        "resolve it before recording a new decision"
                    )
                if heads:
                    current = self._approval_from_row(heads[0])
                    if (
                        current.obtained == approval.obtained
                        and current.authority == approval.authority
                        and current.evidence_ref == approval.evidence_ref
                        and current.decision == approval.decision
                        and current.pricing_baseline_id == pricing_baseline_id
                    ):
                        conn.rollback()
                        return "unchanged"
                    if current.obtained and not approval.obtained:
                        raise ValueError(
                            "This Bid gate approval is already recorded as obtained; "
                            "an obtained approval cannot be replaced here"
                        )
                    if current.obtained and current.pricing_baseline_id == pricing_baseline_id:
                        raise ValueError(
                            "This Bid gate approval is already recorded as obtained for "
                            "the currently selected pricing baseline"
                        )
                    if not approval.obtained:
                        raise ValueError(
                            "This Bid gate approval is already recorded as not obtained; "
                            "record the obtained decision once it has been made"
                        )
                    if expected_current_approval_id != current.approval_id:
                        raise ValueError(
                            "This Bid gate approval changed since the page was loaded; "
                            "reload and review the current decision"
                        )
                    supersedes = current.approval_id
                elif expected_current_approval_id:
                    raise ValueError(
                        "This Bid gate approval changed since the page was loaded; "
                        "reload and review the current decision"
                    )
                at = datetime.now(UTC)
                conn.execute(
                    """
                    INSERT INTO approvals (
                        approval_id, bid_id, approval_type, required, obtained,
                        authority, evidence_ref, decision, decided_at, provenance_json,
                        supersedes_approval_id, recorded_at, pricing_baseline_id
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        approval.approval_id,
                        approval.bid_id,
                        approval.approval_type.value,
                        int(approval.required),
                        int(approval.obtained),
                        approval.authority,
                        approval.evidence_ref,
                        approval.decision,
                        approval.decided_at.isoformat() if approval.decided_at else None,
                        approval.provenance.model_dump_json(),
                        supersedes,
                        at.isoformat(),
                        pricing_baseline_id,
                    ),
                )
                conn.execute(
                    """INSERT INTO audit_log(
                    entry_id,bid_id,actor,action,detail,timestamp) VALUES(?,?,?,?,?,?)""",
                    (
                        f"AUD-{uuid4()}",
                        approval.bid_id,
                        actor,
                        "bid_gate_approval_superseded"
                        if supersedes
                        else "bid_gate_approval_created",
                        json.dumps(
                            {
                                "approval_id": approval.approval_id,
                                "gate": approval.approval_type.value,
                                "decision": approval.decision,
                                "obtained": approval.obtained,
                                "supersedes_approval_id": supersedes,
                            },
                            sort_keys=True,
                        ),
                        at.isoformat(),
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return "superseded" if supersedes else "created"

    def update_approval(self, approval: Approval) -> None:
        """Reject in-place edits of a gate approval decision.

        Approvals are append-only evidence. Use :meth:`record_gate_approval` to append a
        superseding decision; the earlier decision stays as history.

        Raises:
            ValueError: Always.
        """
        raise ValueError(
            "Gate approvals cannot be edited in place; use record_gate_approval to append "
            f"a superseding decision for {approval.approval_id}"
        )

    @staticmethod
    def _gate_record_from_row(row: sqlite3.Row) -> GateRecord:
        blocker_values = cast(list[str], json.loads(str(row["blockers"])))
        decided_at = BidRepository._optional_str(row["decided_at"])
        return GateRecord(
            bid_id=str(row["bid_id"]),
            gate=Gate(str(row["gate"])),
            status=GateStatus(str(row["status"])),
            blockers=blocker_values,
            override_by=BidRepository._optional_str(row["override_by"]),
            override_risk_note=BidRepository._optional_str(row["override_risk_note"]),
            decided_at=datetime.fromisoformat(decided_at) if decided_at is not None else None,
        )

    def upsert_gate_record(self, record: GateRecord) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO gate_records (
                    bid_id, gate, status, blockers, override_by,
                    override_risk_note, decided_at
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(bid_id, gate) DO UPDATE SET
                    status = excluded.status,
                    blockers = excluded.blockers,
                    override_by = excluded.override_by,
                    override_risk_note = excluded.override_risk_note,
                    decided_at = excluded.decided_at
                """,
                (
                    record.bid_id,
                    record.gate.value,
                    record.status.value,
                    json.dumps(record.blockers),
                    record.override_by,
                    record.override_risk_note,
                    record.decided_at.isoformat() if record.decided_at else None,
                ),
            )
            conn.commit()

    def get_gate_record(self, bid_id: str, gate: Gate) -> GateRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM gate_records WHERE bid_id = ? AND gate = ?",
                (bid_id, gate.value),
            ).fetchone()
        return self._gate_record_from_row(row) if row is not None else None

    def list_gate_records(self, bid_id: str) -> list[GateRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM gate_records WHERE bid_id = ? ORDER BY gate",
                (bid_id,),
            ).fetchall()
        return [self._gate_record_from_row(row) for row in rows]

    @staticmethod
    def _audit_from_row(row: sqlite3.Row) -> AuditEntry:
        return AuditEntry(
            entry_id=str(row["entry_id"]),
            bid_id=BidRepository._optional_str(row["bid_id"]),
            actor=str(row["actor"]),
            action=str(row["action"]),
            detail=str(row["detail"]),
            timestamp=datetime.fromisoformat(str(row["timestamp"])),
        )

    def append_audit(self, entry: AuditEntry) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO audit_log (
                    entry_id, bid_id, actor, action, detail, timestamp
                ) VALUES (?,?,?,?,?,?)
                """,
                (
                    entry.entry_id,
                    entry.bid_id,
                    entry.actor,
                    entry.action,
                    entry.detail,
                    entry.timestamp.isoformat(),
                ),
            )
            conn.commit()

    def list_audit(self, bid_id: str | None = None) -> list[AuditEntry]:
        with self._conn() as conn:
            if bid_id is None:
                rows = conn.execute(
                    "SELECT * FROM audit_log ORDER BY timestamp, entry_id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit_log WHERE bid_id = ? ORDER BY timestamp, entry_id",
                    (bid_id,),
                ).fetchall()
        return [self._audit_from_row(row) for row in rows]
