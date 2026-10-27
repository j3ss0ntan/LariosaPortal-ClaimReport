"""Eurolink claims submission dashboard -- Flask port (no Streamlit)."""
from __future__ import annotations

import logging
import secrets as pysecrets
import time
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint, Flask, g, redirect, render_template, request, session, url_for,
    flash, get_flashed_messages, abort,
)
from werkzeug.exceptions import HTTPException

import auth
import charts
import data as D
import db

ROOT = Path(__file__).parent
SECRET_KEY_FILE = ROOT / ".flask_secret_key"
REFRESH_MIN = 10
MAX_UPLOAD_MB = 100
URL_PREFIX = "/lariosa/eclaims-report"

logging.basicConfig(level=logging.WARNING)

AUDIT_ACTIONS = [
    "login_success", "login_failed", "logout", "password_changed",
    "password_reset", "user_created", "user_removed", "role_changed",
    "drive_url_changed", "data_refreshed", "file_uploaded", "file_upload_failed",
    "migration_run",
]


def _load_secret_key() -> str:
    if SECRET_KEY_FILE.exists():
        return SECRET_KEY_FILE.read_text().strip()
    key = pysecrets.token_hex(32)
    SECRET_KEY_FILE.write_text(key)
    return key


app = Flask(__name__)
app.secret_key = _load_secret_key()
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

bp = Blueprint("eclaims", __name__, url_prefix=URL_PREFIX)


@app.route("/")
def _root_blank():
    return ""


@app.after_request
def _no_cache(resp):
    """Dynamic pages (and the root redirect) must never be served stale from
    the browser's cache -- a cached 302 or a cached old dashboard HTML is
    exactly what makes a URL/route change look like "a blank page that won't
    redirect" after a deploy."""
    if resp.mimetype == "text/html" or resp.status_code in (301, 302, 303, 307, 308):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
    return resp


@app.errorhandler(HTTPException)
def _handle_http_exception(e: HTTPException):
    """Surface request-level rejections (bad/oversized upload, etc.) as a flash
    instead of a bare error page, and log the real reason since debug mode is off."""
    app.logger.warning(
        "HTTP %s on %s %s: %s (content-length=%s, content-type=%s)",
        e.code, request.method, request.path, e.description,
        request.content_length, request.content_type,
    )
    if request.path == f"{URL_PREFIX}/upload" and e.code == 413:
        flash(f"That file is larger than the {MAX_UPLOAD_MB} MB upload limit.", "error")
    elif request.path == f"{URL_PREFIX}/upload":
        flash(f"The upload could not be processed ({e.code} {e.name}). Please try again.", "error")
    else:
        flash(f"Request error: {e.code} {e.name}.", "error")
    return redirect(url_for("eclaims.dashboard" if session.get("auth_user") else "eclaims.login"))

_cache: dict[str, tuple[float, D.Claims, str]] = {}
_upload_cache: tuple[str, D.Claims, str] | None = None  # (uploaded_at, claims, source_label)


def load_claims(drive_url: str | None) -> tuple[D.Claims, str]:
    key = drive_url or ""
    now = time.time()
    cached = _cache.get(key)
    if cached and cached[0] > now:
        return cached[1], cached[2]
    text, source = D.fetch_sheet(drive_url or None)
    rows = D.rows_from_text(text)
    claims = D.compute(rows)
    _cache[key] = (now + REFRESH_MIN * 60, claims, source)
    return claims, source


def clear_cache() -> None:
    _cache.clear()


def load_persisted_claims() -> tuple[D.Claims, str] | None:
    """The dataset from the most recent file upload, if one is stored. Overwritten on every new upload."""
    global _upload_cache
    row = db.get_claims_source()
    if not row:
        return None
    if _upload_cache and _upload_cache[0] == row["uploaded_at"]:
        return _upload_cache[1], _upload_cache[2]
    rows_df = D.rows_from_text(row["csv_text"])
    claims = D.compute(rows_df)
    _upload_cache = (row["uploaded_at"], claims, row["source_label"])
    return claims, row["source_label"]


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        username = session.get("auth_user")
        if not username:
            return redirect(url_for(".login"))
        user = auth.all_users().get(username)
        if user is None:
            session.pop("auth_user", None)
            flash("Your account no longer exists. Please contact an administrator.", "error")
            return redirect(url_for(".login"))
        g.username = username
        g.user = user
        if user.get("must_change_password") and request.endpoint not in ("eclaims.change_password", "eclaims.logout"):
            return redirect(url_for(".change_password"))
        return f(*args, **kwargs)

    return wrapper


def role_required(role: str):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not auth.has_at_least(g.user.get("role"), role):
                abort(403)
            return f(*args, **kwargs)

        return wrapper

    return deco


@app.context_processor
def inject_common():
    return {
        "has_at_least": auth.has_at_least,
        "role": g.user.get("role") if getattr(g, "user", None) else None,
    }


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("auth_user"):
        return redirect(url_for(".dashboard"))
    error = None
    if request.method == "POST":
        uname = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if auth.authenticate(uname, password):
            session["auth_user"] = uname
            db.log_audit(actor=uname, action="login_success", target=uname)
            return redirect(url_for(".dashboard"))
        db.log_audit(actor=uname or None, action="login_failed", target=uname or None)
        error = "Incorrect username or password."
    return render_template("login.html", error=error)


@bp.route("/logout", methods=["POST"])
def logout():
    username = session.pop("auth_user", None)
    if username:
        db.log_audit(actor=username, action="logout", target=username)
    return redirect(url_for(".login"))


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if not g.user.get("must_change_password"):
        return redirect(url_for(".dashboard"))
    error = None
    if request.method == "POST":
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")
        if new_pw != confirm_pw:
            error = "Passwords do not match."
        else:
            try:
                auth.change_own_password(g.username, new_pw)
                return redirect(url_for(".dashboard"))
            except ValueError as e:
                error = str(e)
    return render_template("change_password.html", error=error, policy=auth.get_password_policy())


@bp.route("/data-source", methods=["POST"])
@login_required
@role_required("editor")
def set_data_source():
    new_url = request.form.get("drive_url", "").strip()
    prev = session.get("drive_url", "")
    if new_url != prev:
        db.log_audit(actor=g.username, action="drive_url_changed", details={"new_url": new_url or None})
    session["drive_url"] = new_url
    db.clear_claims_source()
    global _upload_cache
    _upload_cache = None
    return redirect(url_for(".dashboard"))


@bp.route("/refresh", methods=["POST"])
@login_required
@role_required("editor")
def refresh():
    db.clear_claims_source()
    global _upload_cache
    _upload_cache = None
    clear_cache()
    db.log_audit(actor=g.username, action="data_refreshed")
    return redirect(url_for(".dashboard"))


@bp.route("/upload", methods=["POST"])
@login_required
@role_required("editor")
def upload():
    file = request.files.get("file")
    if not file or not file.filename:
        return redirect(url_for(".dashboard"))
    try:
        file_bytes = file.read()
        rows, source_label = D.rows_from_file(file.filename, file_bytes)
        D.compute(rows)  # validate before persisting
        db.save_claims_source(rows.to_csv(index=False), source_label, uploaded_by=g.username)
        global _upload_cache
        _upload_cache = None
        db.log_audit(
            actor=g.username, action="file_uploaded", target=file.filename,
            details={"size_bytes": len(file_bytes), "rows": len(rows)},
        )
        flash(
            f"Uploaded {file.filename}. This replaces the current data for everyone until a new "
            "file is uploaded or the data is refreshed from the live source.",
            "success",
        )
        return redirect(url_for(".dashboard"))
    except Exception as e:  # noqa: BLE001
        db.log_audit(actor=g.username, action="file_upload_failed", target=file.filename, details={"error": str(e)})
        return _render_dashboard(None, None, error=f"Could not read the data. {e}")


@bp.route("/admin/users", methods=["POST"])
@login_required
@role_required("admin")
def admin_users():
    action = request.form.get("action", "")
    try:
        if action == "create":
            username = request.form.get("username", "").strip()
            name = request.form.get("name", "").strip()
            role = request.form.get("role", "viewer")
            if not username:
                raise ValueError("Username is required.")
            temp_pw = auth.generate_temp_password()
            auth.create_user(username, temp_pw, role, name, actor=g.username)
            flash(f"CREATE\x1f{username}\x1f{role}\x1f{temp_pw}", "secret")
        elif action == "set_role":
            username = request.form.get("username", "")
            role = request.form.get("role", "")
            auth.set_role(username, role, actor=g.username)
            flash(f"{username} is now {role}.", "success")
        elif action == "reset_password":
            username = request.form.get("username", "")
            temp_pw = auth.reset_password(username, actor=g.username)
            flash(f"RESET\x1f{username}\x1f{temp_pw}", "secret")
        elif action == "remove":
            username = request.form.get("username", "")
            auth.remove_user(username, actor=g.username)
            flash(f"Removed {username}.", "success")
        elif action == "set_password_policy":
            min_length = int(request.form.get("min_length", 8) or 8)
            auth.save_password_policy(
                require_min_length="require_min_length" in request.form,
                min_length=min_length,
                require_lower="require_lower" in request.form,
                require_upper="require_upper" in request.form,
                require_digit="require_digit" in request.form,
                require_special="require_special" in request.form,
                actor=g.username,
            )
            flash("Password policy updated.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for(".dashboard"))


def _render_dashboard(claims, source_label, error: str | None = None):
    can_edit = auth.has_at_least(g.user.get("role"), "editor")
    can_admin = auth.has_at_least(g.user.get("role"), "admin")

    users = auth.all_users() if can_admin else {}
    audit_filter = request.args.get("audit_action", "(all)")
    audit_rows = []
    password_policy = None
    if can_admin:
        audit_rows = db.recent_audit(limit=200, action=None if audit_filter == "(all)" else audit_filter)
        password_policy = auth.get_password_policy()

    ctx = dict(
        username=g.username,
        name=g.user.get("name", g.username),
        role=g.user.get("role", "viewer"),
        can_edit=can_edit,
        can_admin=can_admin,
        drive_url=session.get("drive_url", ""),
        users=users,
        valid_roles=auth.VALID_ROLES,
        default_admin=auth.DEFAULT_ADMIN_USERNAME,
        audit_actions=AUDIT_ACTIONS,
        audit_filter=audit_filter,
        audit_rows=audit_rows,
        password_policy=password_policy,
        error=error,
        claims=None,
        now=datetime.now().strftime("%b %d, %Y %I:%M %p"),
    )

    if error or claims is None:
        return render_template("dashboard.html", **ctx)

    meta = claims.meta
    summary = claims.summary.set_index("type")
    labels = {"IP": "Inpatient (IP)", "OPD": "Outpatient (OPD)", "HEMO": "Hemodialysis (HEMO)"}
    kpis = []
    for t in charts.TYPES:
        row = summary.loc[t]
        kpis.append(dict(
            label=labels[t], claims=f"{int(row['claims']):,}",
            avg_delay=row["avg_delay"] or "—",
            late_pct=row["late_pct"] or "—",
            per_day_recent=row["per_day_recent"] or "—",
        ))

    plot_kwargs = dict(full_html=False, include_plotlyjs=False, config={"displayModeBar": False})
    chart_html = dict(
        compare=charts.compare_chart(claims.monthly).to_html(**plot_kwargs),
        per_day=charts.line_chart(claims.monthly, "month", "per_day").to_html(**plot_kwargs),
        avg_delay=charts.line_chart(claims.monthly, "month", "avg_delay", ref=60, y_max=120, title_unit=" d").to_html(**plot_kwargs),
        daily=charts.line_chart(claims.daily, "day", "claims").to_html(**plot_kwargs),
        buckets=charts.bucket_chart(claims.buckets).to_html(**plot_kwargs),
        status=charts.status_chart(claims.status).to_html(**plot_kwargs),
    )

    active_months = [m for m in claims.calendar if m["active"]]
    calendar_months = [
        dict(
            title=m["date"].strftime("%B %Y"), tot=m["tot"],
            chart_html=charts.calendar_heatmap(m).to_html(**plot_kwargs),
        )
        for m in reversed(active_months)
    ]
    inactive = [m["date"].strftime("%b %Y") for m in claims.calendar if not m["active"]]

    ctx.update(
        source_label=source_label,
        meta=meta,
        kpis=kpis,
        chart_html=chart_html,
        calendar_months=calendar_months,
        inactive=", ".join(inactive),
    )
    return render_template("dashboard.html", **ctx)


@bp.route("/")
@login_required
def dashboard():
    try:
        persisted = load_persisted_claims()
    except Exception as e:  # noqa: BLE001
        return _render_dashboard(None, None, error=f"Could not read the saved uploaded data. {e}")
    if persisted:
        claims, source_label = persisted
        return _render_dashboard(claims, source_label)

    drive_url = session.get("drive_url", "")
    try:
        claims, source_label = load_claims(drive_url)
    except D.SourceError as e:
        error = (
            "The Google Sheet could not be read from this page. Tried: "
            f"{'; '.join(e.tried)}. To load it live, open the sheet, choose "
            "Share → General access → Anyone with the link → Viewer, then refresh. "
            "You can also load a file instead."
        )
        return _render_dashboard(None, None, error=error)
    except Exception as e:  # noqa: BLE001
        return _render_dashboard(None, None, error=f"Could not read the data. {e}")
    return _render_dashboard(claims, source_label)


app.register_blueprint(bp)


if __name__ == "__main__":
    import os
    port = int(os.environ.get("FLASK_RUN_PORT", 8501))
    app.run(host="127.0.0.1", port=port, debug=False)
