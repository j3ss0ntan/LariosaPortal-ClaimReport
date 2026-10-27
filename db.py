"""Shared SQLite connection, schema, and audit-log helpers.

Run `python migrate.py` to create/upgrade the schema and import any
legacy users.json into it -- the supported, re-runnable setup path.
The app also creates the schema defensively on startup (CREATE TABLE IF
NOT EXISTS) so it never hard-crashes on a missing database, but migrate.py
is what you run after cloning the repo or pulling a schema change.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "eurolink.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('viewer','editor','admin')),
    must_change_password INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims_source (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    csv_text TEXT NOT NULL,
    source_label TEXT NOT NULL,
    uploaded_by TEXT,
    uploaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_policy (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    require_min_length INTEGER NOT NULL DEFAULT 1,
    min_length INTEGER NOT NULL DEFAULT 8,
    require_lower INTEGER NOT NULL DEFAULT 0,
    require_upper INTEGER NOT NULL DEFAULT 0,
    require_digit INTEGER NOT NULL DEFAULT 0,
    require_special INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT,
    updated_by TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT,
    action TEXT NOT NULL,
    target TEXT,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_audit(actor: Optional[str], action: str, target: Optional[str] = None, details: Optional[dict] = None) -> None:
    """Append one row to audit_log. Never raises -- a logging failure must not break the transaction it's logging."""
    try:
        ensure_schema()
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO audit_log (ts, actor, action, target, details) VALUES (?, ?, ?, ?, ?)",
                (now(), actor, action, target, json.dumps(details) if details is not None else None),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def save_claims_source(csv_text: str, source_label: str, uploaded_by: Optional[str]) -> None:
    """Overwrite the single stored claims dataset with a newly uploaded one."""
    ensure_schema()
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO claims_source (id, csv_text, source_label, uploaded_by, uploaded_at) "
            "VALUES (1, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "csv_text = excluded.csv_text, source_label = excluded.source_label, "
            "uploaded_by = excluded.uploaded_by, uploaded_at = excluded.uploaded_at",
            (csv_text, source_label, uploaded_by, now()),
        )
        conn.commit()
    finally:
        conn.close()


def get_claims_source() -> Optional[dict]:
    ensure_schema()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT csv_text, source_label, uploaded_by, uploaded_at FROM claims_source WHERE id = 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def clear_claims_source() -> None:
    ensure_schema()
    conn = get_conn()
    try:
        conn.execute("DELETE FROM claims_source WHERE id = 1")
        conn.commit()
    finally:
        conn.close()


_DEFAULT_PASSWORD_POLICY = {
    "require_min_length": 1, "min_length": 8,
    "require_lower": 0, "require_upper": 0, "require_digit": 0, "require_special": 0,
}


def get_password_policy() -> dict:
    ensure_schema()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT require_min_length, min_length, require_lower, require_upper, "
            "require_digit, require_special FROM password_policy WHERE id = 1"
        ).fetchone()
        return dict(row) if row else dict(_DEFAULT_PASSWORD_POLICY)
    finally:
        conn.close()


def save_password_policy(
    require_min_length: bool, min_length: int, require_lower: bool,
    require_upper: bool, require_digit: bool, require_special: bool,
    updated_by: Optional[str] = None,
) -> None:
    ensure_schema()
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO password_policy "
            "(id, require_min_length, min_length, require_lower, require_upper, require_digit, require_special, updated_at, updated_by) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "require_min_length = excluded.require_min_length, min_length = excluded.min_length, "
            "require_lower = excluded.require_lower, require_upper = excluded.require_upper, "
            "require_digit = excluded.require_digit, require_special = excluded.require_special, "
            "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (
                int(require_min_length), min_length, int(require_lower),
                int(require_upper), int(require_digit), int(require_special),
                now(), updated_by,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def recent_audit(limit: int = 200, actor: Optional[str] = None, action: Optional[str] = None) -> list[dict]:
    ensure_schema()
    conn = get_conn()
    try:
        q = "SELECT ts, actor, action, target, details FROM audit_log"
        clauses, params = [], []
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(q, params).fetchall()]
    finally:
        conn.close()
