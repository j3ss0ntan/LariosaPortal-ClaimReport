"""Manual migration: create/upgrade the SQLite schema (users, audit_log)
and import any existing users.json into the users table.

Run it yourself after cloning the repo or pulling a schema change:

    python migrate.py

Safe to run as many times as you like:
- schema creation is idempotent (CREATE TABLE IF NOT EXISTS)
- the users.json import only INSERTs usernames not already in the
  database, so it never overwrites a password or role change you've
  already made in the database
- the default admin is seeded only if missing
"""
from __future__ import annotations

import json
from pathlib import Path

import bcrypt

import db

ROOT = Path(__file__).parent
USERS_JSON = ROOT / "users.json"
DEFAULT_ADMIN_USERNAME = "jessontan"
DEFAULT_ADMIN_PASSWORD = "ChangeMe123!"


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def main() -> None:
    print(f"Database: {db.DB_PATH}")
    db.ensure_schema()
    print("Schema OK (users, audit_log).")

    conn = db.get_conn()
    try:
        imported = 0
        if USERS_JSON.exists():
            legacy = json.loads(USERS_JSON.read_text())
            for username, info in legacy.items():
                if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                    continue
                ts = db.now()
                conn.execute(
                    "INSERT INTO users (username, name, password_hash, role, must_change_password, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        username,
                        info.get("name", username),
                        info["password_hash"],
                        info.get("role", "viewer"),
                        1 if info.get("must_change_password", True) else 0,
                        ts,
                        ts,
                    ),
                )
                imported += 1
            conn.commit()
            if imported:
                print(f"Imported {imported} user(s) from {USERS_JSON.name} (already-present usernames were skipped).")
            else:
                print(f"{USERS_JSON.name} found, but every user in it already exists in the database -- nothing to import.")
        else:
            print(f"No {USERS_JSON.name} found -- skipping legacy import.")

        if not conn.execute("SELECT 1 FROM users WHERE username = ?", (DEFAULT_ADMIN_USERNAME,)).fetchone():
            ts = db.now()
            conn.execute(
                "INSERT INTO users (username, name, password_hash, role, must_change_password, created_at, updated_at) "
                "VALUES (?, 'Jesson Tan', ?, 'admin', 1, ?, ?)",
                (DEFAULT_ADMIN_USERNAME, _hash(DEFAULT_ADMIN_PASSWORD), ts, ts),
            )
            conn.commit()
            print(f"Seeded default admin '{DEFAULT_ADMIN_USERNAME}' "
                  f"(password: {DEFAULT_ADMIN_PASSWORD} -- must be changed on first login).")
        else:
            print(f"Default admin '{DEFAULT_ADMIN_USERNAME}' already present.")

        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        print(f"Total users in database: {total}")
    finally:
        conn.close()

    db.log_audit(actor="migrate.py", action="migration_run", details={"users_json_found": USERS_JSON.exists()})
    print("Migration complete.")


if __name__ == "__main__":
    main()
