"""
Case Management — storage layer.

Plain sqlite3 (stdlib, no extra dependency) rather than an ORM — this is
a small, well-scoped schema and the project already favors simple,
readable code over heavier frameworks. One connection per CaseStore
instance, safe for the single-process FastAPI app this runs in.
"""

import sqlite3
import json
from typing import Optional
from .models import Case, Note, AuditEvent, CaseStatus, Severity, Priority, ALLOWED_STATUS_TRANSITIONS, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    source_pipeline TEXT NOT NULL,
    exception_code TEXT NOT NULL,
    severity TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL,
    record_json TEXT NOT NULL,
    assignee TEXT,
    resolution_note TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(case_id) REFERENCES cases(case_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL,
    actor TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(case_id) REFERENCES cases(case_id)
);

CREATE TABLE IF NOT EXISTS webhook_events (
    event_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payment_id TEXT,
    entity_id TEXT,
    amount REAL,
    payload_hash TEXT NOT NULL,
    processed_at TEXT,
    status TEXT NOT NULL,
    duplicate INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_severity ON cases(severity);
CREATE INDEX IF NOT EXISTS idx_cases_priority ON cases(priority);
CREATE INDEX IF NOT EXISTS idx_cases_exception_code ON cases(exception_code);
CREATE INDEX IF NOT EXISTS idx_webhook_events_status ON webhook_events(status);
"""


class CaseNotFoundError(Exception):
    pass


class InvalidStatusTransitionError(Exception):
    pass


class CaseStore:
    def __init__(self, db_path: str = "case_management/cases.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # -- creation / ingestion -------------------------------------------------

    def create_case(self, case_id: str, source_pipeline: str, exception_code: str,
                     severity: Severity, priority: Priority, record: dict,
                     actor: str = "system") -> Case:
        ts = now_iso()
        self.conn.execute(
            "INSERT OR IGNORE INTO cases "
            "(case_id, source_pipeline, exception_code, severity, priority, status, "
            " record_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (case_id, source_pipeline, exception_code, severity.value, priority.value,
             CaseStatus.OPEN.value, json.dumps(record, default=str), ts, ts),
        )
        self.conn.commit()
        self._log_event(case_id, "created", f"Case created from {source_pipeline} ({exception_code})", actor=actor)
        return self.get_case(case_id)

    # -- reads ------------------------------------------------------------------

    def get_case(self, case_id: str) -> Case:
        row = self.conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise CaseNotFoundError(case_id)
        return _row_to_case(row)

    def list_cases(self, exception_code: Optional[str] = None, status: Optional[str] = None,
                   severity: Optional[str] = None, priority: Optional[str] = None,
                   limit: int = 100, offset: int = 0) -> list:
        query = "SELECT * FROM cases WHERE 1=1"
        params = []
        if exception_code:
            query += " AND exception_code = ?"
            params.append(exception_code)
        if status:
            query += " AND status = ?"
            params.append(status)
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        if priority:
            query += " AND priority = ?"
            params.append(priority)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self.conn.execute(query, params).fetchall()
        return [_row_to_case(r) for r in rows]

    def count_cases(self, exception_code: Optional[str] = None, status: Optional[str] = None,
                     severity: Optional[str] = None, priority: Optional[str] = None) -> int:
        query = "SELECT COUNT(*) as c FROM cases WHERE 1=1"
        params = []
        if exception_code:
            query += " AND exception_code = ?"
            params.append(exception_code)
        if status:
            query += " AND status = ?"
            params.append(status)
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        if priority:
            query += " AND priority = ?"
            params.append(priority)
        return self.conn.execute(query, params).fetchone()["c"]

    def get_audit_history(self, case_id: str) -> list:
        self.get_case(case_id)  # raises CaseNotFoundError if missing
        rows = self.conn.execute(
            "SELECT * FROM audit_events WHERE case_id = ? ORDER BY event_id ASC", (case_id,)
        ).fetchall()
        return [AuditEvent(event_id=r["event_id"], case_id=r["case_id"], event_type=r["event_type"],
                            detail=r["detail"], actor=r["actor"], created_at=r["created_at"]) for r in rows]

    def get_notes(self, case_id: str) -> list:
        self.get_case(case_id)
        rows = self.conn.execute(
            "SELECT * FROM notes WHERE case_id = ? ORDER BY note_id ASC", (case_id,)
        ).fetchall()
        return [Note(note_id=r["note_id"], case_id=r["case_id"], author=r["author"],
                      text=r["text"], created_at=r["created_at"]) for r in rows]

    # -- mutations ----------------------------------------------------------

    def update_status(self, case_id: str, new_status: CaseStatus, actor: str, reason: Optional[str] = None) -> Case:
        case = self.get_case(case_id)
        current = CaseStatus(case.status)

        if new_status == CaseStatus.RESOLVED:
            raise InvalidStatusTransitionError(
                "Use the /resolve endpoint to resolve a case (requires a resolution note)."
            )
        if new_status not in ALLOWED_STATUS_TRANSITIONS.get(current, set()):
            raise InvalidStatusTransitionError(f"Cannot move case from '{current.value}' to '{new_status.value}'.")
        if new_status == CaseStatus.ESCALATED:
            if not reason or not reason.strip():
                raise InvalidStatusTransitionError("reason is required to escalate a case")

        ts = now_iso()
        self.conn.execute("UPDATE cases SET status = ?, updated_at = ? WHERE case_id = ?",
                           (new_status.value, ts, case_id))
        self.conn.commit()

        detail = f"Status changed: {current.value} -> {new_status.value}"
        if reason and reason.strip():
            detail += f" | reason: {reason.strip()}"
        detail += f" | actor: {actor} | timestamp: {ts}"
        self._log_event(case_id, "status_changed", detail, actor)
        return self.get_case(case_id)

    def assign_case(self, case_id: str, assignee: str, actor: str) -> Case:
        self.get_case(case_id)  # raises if missing
        ts = now_iso()
        self.conn.execute("UPDATE cases SET assignee = ?, updated_at = ? WHERE case_id = ?",
                           (assignee, ts, case_id))
        self.conn.commit()
        self._log_event(case_id, "assigned", f"Assigned to {assignee}", actor)
        return self.get_case(case_id)

    def add_note(self, case_id: str, author: str, text: str) -> Note:
        self.get_case(case_id)  # raises if missing
        ts = now_iso()
        cursor = self.conn.execute(
            "INSERT INTO notes (case_id, author, text, created_at) VALUES (?,?,?,?)",
            (case_id, author, text, ts),
        )
        self.conn.commit()
        note_id = cursor.lastrowid
        self.conn.execute("UPDATE cases SET updated_at = ? WHERE case_id = ?", (ts, case_id))
        self.conn.commit()
        self._log_event(case_id, "note_added", f"Note added by {author}", author)
        return Note(note_id=note_id, case_id=case_id, author=author, text=text, created_at=ts)

    def resolve_case(self, case_id: str, resolved_by: str, resolution_note: str) -> Case:
        case = self.get_case(case_id)
        current = CaseStatus(case.status)
        if current == CaseStatus.RESOLVED:
            raise InvalidStatusTransitionError("Case is already resolved.")
        if not resolution_note or not resolution_note.strip():
            raise InvalidStatusTransitionError("resolution_note is required to resolve a case")
        if CaseStatus.RESOLVED not in ALLOWED_STATUS_TRANSITIONS.get(current, set()):
            raise InvalidStatusTransitionError(f"Cannot resolve case from '{current.value}' to '{CaseStatus.RESOLVED.value}'.")

        ts = now_iso()
        self.conn.execute(
            "UPDATE cases SET status = ?, resolution_note = ?, resolved_by = ?, "
            "resolved_at = ?, updated_at = ? WHERE case_id = ?",
            (CaseStatus.RESOLVED.value, resolution_note.strip(), resolved_by, ts, ts, case_id),
        )
        self.conn.commit()
        detail = f"Resolved: {current.value} -> {CaseStatus.RESOLVED.value} | resolved_by: {resolved_by} | timestamp: {ts} | reason: {resolution_note.strip()}"
        self._log_event(case_id, "resolved", detail, resolved_by)
        return self.get_case(case_id)

    # -- webhooks -------------------------------------------------------------

    def get_webhook_event(self, event_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM webhook_events WHERE event_id = ?", (event_id,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def record_webhook_event(
        self,
        event_id: str,
        event_type: str,
        payload_hash: str,
        status: str = "received",
        payment_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        amount: Optional[float] = None,
        duplicate: bool = False,
    ) -> bool:
        """Records an incoming webhook event. Returns True if newly inserted, False if event_id already exists."""
        ts = now_iso()
        existing = self.get_webhook_event(event_id)
        if existing is not None:
            self.conn.execute(
                "UPDATE webhook_events SET duplicate = 1, status = ?, processed_at = COALESCE(processed_at, ?), payment_id = COALESCE(?, payment_id), entity_id = COALESCE(?, entity_id), amount = COALESCE(?, amount) WHERE event_id = ?",
                (status, ts, payment_id, entity_id, amount, event_id),
            )
            self.conn.commit()
            return False

        cursor = self.conn.execute(
            "INSERT INTO webhook_events (event_id, received_at, event_type, payment_id, entity_id, amount, payload_hash, status, processed_at, duplicate) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (event_id, ts, event_type, payment_id, entity_id, amount, payload_hash, status, None if status == "received" else ts, 1 if duplicate else 0),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def update_webhook_event_status(
        self,
        event_id: str,
        status: str,
        payment_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        amount: Optional[float] = None,
        duplicate: bool = False,
    ) -> None:
        ts = now_iso()
        self.conn.execute(
            "UPDATE webhook_events SET status = ?, processed_at = ?, payment_id = COALESCE(?, payment_id), entity_id = COALESCE(?, entity_id), amount = COALESCE(?, amount), duplicate = ? WHERE event_id = ?",
            (status, ts, payment_id, entity_id, amount, 1 if duplicate else 0, event_id),
        )
        self.conn.commit()

    def list_webhook_events(self, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            "SELECT event_id, event_type, payment_id, entity_id, amount, status, duplicate, received_at, processed_at FROM webhook_events ORDER BY received_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_webhook_monitor(self, limit: int = 10) -> dict:
        total = self.conn.execute("SELECT COUNT(*) as c FROM webhook_events").fetchone()["c"]
        latest = self.conn.execute(
            "SELECT received_at FROM webhook_events ORDER BY received_at DESC LIMIT 1"
        ).fetchone()
        rows = self.list_webhook_events(limit=limit)
        return {
            "total_events": total,
            "last_received_at": latest["received_at"] if latest else None,
            "events": rows,
        }

    # -- internal -------------------------------------------------------------

    def _log_event(self, case_id: str, event_type: str, detail: str, actor: Optional[str]):
        ts = now_iso()
        self.conn.execute(
            "INSERT INTO audit_events (case_id, event_type, detail, actor, created_at) VALUES (?,?,?,?,?)",
            (case_id, event_type, detail, actor, ts),
        )
        self.conn.commit()


def _row_to_case(row: sqlite3.Row) -> Case:
    return Case(
        case_id=row["case_id"], source_pipeline=row["source_pipeline"],
        exception_code=row["exception_code"], severity=row["severity"], priority=row["priority"],
        status=row["status"], record_json=row["record_json"], assignee=row["assignee"],
        resolution_note=row["resolution_note"], resolved_by=row["resolved_by"],
        resolved_at=row["resolved_at"], created_at=row["created_at"], updated_at=row["updated_at"],
    )
