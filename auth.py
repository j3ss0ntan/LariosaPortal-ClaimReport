"""Username/password RBAC, backed by SQLite (see db.py / migrate.py).

Pure business logic -- no web framework dependency. app.py (Flask) owns the
session/cookie and calls authenticate()/change_own_password()/require_login
equivalents via its own route handlers and decorators.

Roles: admin > editor > viewer.
- viewer: read-only dashboard.
- editor: viewer + sidebar data-source controls (Drive URL, refresh, file upload).
- admin: editor + user management (create/remove users, change roles, reset passwords).

Users live in the `users` table of eurolink.db (run `python migrate.py` to
set this up and import any legacy users.json). "jessontan" is always
re-seeded as admin if missing, so there is always a way back in. Every
login attempt, logout, password change, and user-management action is
written to the `audit_log` table via db.log_audit.
"""
from __future__ import annotations

import re
import secrets as pysecrets
import string
from typing import Optional

import bcrypt

import db

DEFAULT_ADMIN_USERNAME = "jessontan"
DEFAULT_ADMIN_PASSWORD = "ChangeMe123!"  # must be changed on first login
ROLE_ORDER = {"viewer": 0, "editor": 1, "admin": 2}
VALID_ROLES = list(ROLE_ORDER)
MIN_PASSWORD_LEN = 8  # absolute floor for generated temp passwords, independent of the configured policy
SPECIAL_CHARS = "!@#$%^&*()_+-=[]{}|;:,.<>?/~`'\""


def get_password_policy() -> dict:
    return db.get_password_policy()


def save_password_policy(
    require_min_length: bool, min_length: int, require_lower: bool,
    require_upper: bool, require_digit: bool, require_special: bool, actor: str = "",
) -> None:
    if not (4 <= min_length <= 128):
        raise ValueError("Minimum length must be between 4 and 128.")
    db.save_password_policy(
        require_min_length, min_length, require_lower, require_upper, require_digit, require_special,
        updated_by=actor or None,
    )
    db.log_audit(actor=actor or None, action="password_policy_changed", details={
        "require_min_length": bool(require_min_length), "min_length": min_length,
        "require_lower": bool(require_lower), "require_upper": bool(require_upper),
        "require_digit": bool(require_digit), "require_special": bool(require_special),
    })


def validate_password(password: str, policy: Optional[dict] = None) -> None:
    """Raises ValueError listing every unmet requirement of the configured policy."""
    policy = policy if policy is not None else get_password_policy()
    problems = []
    if policy["require_min_length"] and len(password) < policy["min_length"]:
        problems.append(f"at least {policy['min_length']} characters")
    if policy["require_lower"] and not re.search(r"[a-z]", password):
        problems.append("a lowercase letter")
    if policy["require_upper"] and not re.search(r"[A-Z]", password):
        problems.append("an uppercase letter")
    if policy["require_digit"] and not re.search(r"[0-9]", password):
        problems.append("a number")
    if policy["require_special"] and not any(c in SPECIAL_CHARS for c in password):
        problems.append("a special character")
    if problems:
        raise ValueError("Password must have " + ", ".join(problems) + ".")


def generate_temp_password(policy: Optional[dict] = None) -> str:
    """A random temp password guaranteed to satisfy the configured policy."""
    policy = policy if policy is not None else get_password_policy()
    length = max(policy["min_length"] if policy["require_min_length"] else 0, MIN_PASSWORD_LEN, 12)

    pools = []
    if policy["require_lower"]:
        pools.append(string.ascii_lowercase)
    if policy["require_upper"]:
        pools.append(string.ascii_uppercase)
    if policy["require_digit"]:
        pools.append(string.digits)
    if policy["require_special"]:
        pools.append(SPECIAL_CHARS)
    if not pools:
        pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits]

    chars = [pysecrets.choice(pool) for pool in pools]
    combined = "".join(pools)
    while len(chars) < length:
        chars.append(pysecrets.choice(combined))
    pysecrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def _ensure_admin(conn) -> None:
    if not conn.execute("SELECT 1 FROM users WHERE username = ?", (DEFAULT_ADMIN_USERNAME,)).fetchone():
        ts = db.now()
        conn.execute(
            "INSERT INTO users (username, name, password_hash, role, must_change_password, created_at, updated_at) "
            "VALUES (?, 'Jesson Tan', ?, 'admin', 1, ?, ?)",
            (DEFAULT_ADMIN_USERNAME, _hash(DEFAULT_ADMIN_PASSWORD), ts, ts),
        )
        conn.commit()


def all_users() -> dict:
    db.ensure_schema()
    conn = db.get_conn()
    try:
        _ensure_admin(conn)
        rows = conn.execute("SELECT username, name, role, must_change_password FROM users ORDER BY username").fetchall()
        return {
            r["username"]: {
                "name": r["name"],
                "role": r["role"],
                "must_change_password": bool(r["must_change_password"]),
            }
            for r in rows
        }
    finally:
        conn.close()


def get_role(username: str) -> Optional[str]:
    db.ensure_schema()
    conn = db.get_conn()
    try:
        _ensure_admin(conn)
        row = conn.execute("SELECT role FROM users WHERE username = ?", (username,)).fetchone()
        return row["role"] if row else None
    finally:
        conn.close()


def has_at_least(role: Optional[str], required: str) -> bool:
    if role is None:
        return False
    return ROLE_ORDER.get(role, -1) >= ROLE_ORDER[required]


def user_exists(username: str) -> bool:
    return username in all_users()


def create_user(username: str, password: str, role: str, name: str = "", actor: str = "") -> None:
    if role not in VALID_ROLES:
        raise ValueError(f"Unknown role: {role}")
    validate_password(password)
    db.ensure_schema()
    conn = db.get_conn()
    try:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise ValueError(f"{username} already exists.")
        ts = db.now()
        conn.execute(
            "INSERT INTO users (username, name, password_hash, role, must_change_password, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (username, name or username, _hash(password), role, ts, ts),
        )
        conn.commit()
    finally:
        conn.close()
    db.log_audit(actor=actor or None, action="user_created", target=username, details={"role": role})


def set_role(username: str, role: str, actor: str = "") -> None:
    if role not in VALID_ROLES:
        raise ValueError(f"Unknown role: {role}")
    db.ensure_schema()
    conn = db.get_conn()
    try:
        existing = conn.execute("SELECT role FROM users WHERE username = ?", (username,)).fetchone()
        if not existing:
            raise ValueError(f"Unknown user: {username}")
        old_role = existing["role"]
        conn.execute("UPDATE users SET role = ?, updated_at = ? WHERE username = ?", (role, db.now(), username))
        conn.commit()
    finally:
        conn.close()
    db.log_audit(actor=actor or None, action="role_changed", target=username, details={"from": old_role, "to": role})


def remove_user(username: str, actor: str = "") -> None:
    if username == DEFAULT_ADMIN_USERNAME:
        raise ValueError("The default administrator cannot be removed.")
    db.ensure_schema()
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
    finally:
        conn.close()
    db.log_audit(actor=actor or None, action="user_removed", target=username)


def reset_password(username: str, new_password: Optional[str] = None, actor: str = "") -> str:
    db.ensure_schema()
    temp = new_password or generate_temp_password()
    validate_password(temp)
    conn = db.get_conn()
    try:
        if not conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise ValueError(f"Unknown user: {username}")
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1, updated_at = ? WHERE username = ?",
            (_hash(temp), db.now(), username),
        )
        conn.commit()
    finally:
        conn.close()
    db.log_audit(actor=actor or None, action="password_reset", target=username)
    return temp


def _change_own_password(username: str, new_password: str) -> None:
    db.ensure_schema()
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE username = ?",
            (_hash(new_password), db.now(), username),
        )
        conn.commit()
    finally:
        conn.close()
    db.log_audit(actor=username, action="password_changed", target=username)


def authenticate(username: str, password: str) -> bool:
    db.ensure_schema()
    conn = db.get_conn()
    try:
        _ensure_admin(conn)
        row = conn.execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()
    if not row:
        return False
    return _verify(password, row["password_hash"])


def change_own_password(username: str, new_password: str) -> None:
    """Public wrapper for the forced/self-service password change."""
    validate_password(new_password)
    _change_own_password(username, new_password)
