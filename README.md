# Eurolink Claims Submission Dashboard

Flask port of `Eurolink_Claims_Live.html`. Pulls the Eurolink claims extract
from the Google Sheet (or a Google Drive loader web app, or an uploaded
CSV/XLSX file), classifies each claim as IP / OPD / HEMO, and shows the same
KPIs and charts as the original page. Built on plain Python libraries
(Flask + Jinja2 templates, no Streamlit): a standard WSGI web app that any
Python host can run.

## Sign-in and roles (RBAC)

The app requires a username/password login — no external accounts or setup
needed. Access is role-based:

| Role | Can do |
|---|---|
| `viewer` | View the dashboard. No sidebar controls. |
| `editor` | Viewer + change the Drive loader URL, force a refresh, upload a file. |
| `admin` | Editor + the "User management" panel in the sidebar (create/remove users, change roles, reset passwords). |

Users are stored in the `users` table of `eurolink.db` (a local SQLite file,
gitignored — it's runtime data, not code), bcrypt-hashed passwords.
**`jessontan` is always administrator** — seeded automatically, and
re-seeded if the row is deleted or the database file is wiped, so there's
always a way back in.

**Default admin login (first run):** username `jessontan`, password
`ChangeMe123!`. You'll be required to set a new password immediately on
first login — the temporary password only ever works once.

New accounts created from the admin User management panel work the same
way: the admin sets a temporary password (or lets one be generated), hands
it to the person, and they're forced to change it the first time they log
in.

Login is a signed session cookie (Flask's default session, keyed by a
secret stored in the gitignored `.flask_secret_key`, generated on first
run). It persists across page reloads and new tabs until you log out or
clear cookies — browser-session-scoped, not server-side state.

### Database migration

Run this once after cloning the repo (and again any time the schema
changes):

```
python migrate.py
```

It creates `eurolink.db` with the `users` and `audit_log` tables and, if a
legacy `users.json` from an earlier version of this app is present, imports
any accounts from it that aren't already in the database. It's safe to run
repeatedly — schema creation is idempotent and the import never overwrites
an existing user. The app also creates the schema on its own if you forget
(`CREATE TABLE IF NOT EXISTS`), so it won't hard-crash without it, but
`migrate.py` is the supported, re-runnable setup/upgrade path.

### Audit log

Every login attempt (success or failure), logout, password change,
password reset, user creation/removal, role change, data refresh, Drive URL
change, and file upload (success or failure) is written to the `audit_log`
table — who did it, when, and what the target/details were. Admins can
review it from the "Audit log" panel at the bottom of the sidebar, filterable
by action type. Query it directly for anything beyond the last 200 entries:

```
sqlite3 eurolink.db "select ts, actor, action, target, details from audit_log order by id desc limit 50;"
```

## Run locally

```
pip install -r requirements.txt
python migrate.py
python app.py
```

Opens on http://127.0.0.1:8501. Set `FLASK_RUN_PORT` to use a different port.

## Testing

```
pip install -r requirements-dev.txt
playwright install chromium   # skip if you have Edge/Chrome installed system-wide
python tests/test_app.py
```

`tests/test_app.py` is a Playwright smoke test: it launches the app on a
scratch port, drives it with a real headless browser, logs in as the default
admin, goes through the forced first-login password change, and checks the
dashboard and admin panel render with no JS errors. It reuses the system
Edge/Chrome install if present, so a fresh Chromium download usually isn't
needed.

## Files

- `data.py` — fetching (Google Sheet / Drive loader / file upload) and the
  `compute()` pipeline that reproduces the original dashboard's metrics
  (summary KPIs, status breakdown, aging buckets, monthly/daily series,
  12-month calendar).
- `charts.py` — Plotly chart builders.
- `db.py` — SQLite connection, schema, and audit-log read/write helpers.
- `migrate.py` — manual migration: creates/upgrades the schema and imports legacy `users.json`.
- `auth.py` — username/password authentication and SQLite-backed role store (RBAC); pure business logic, no web framework dependency.
- `app.py` — Flask app: routes, session-based login gate, role-gated views, chart rendering.
- `templates/` — Jinja2 templates (`base.html`, `login.html`, `change_password.html`, `dashboard.html`).
- `static/style.css`, `static/icon.svg`, `static/logo.svg` — styling and branding. Replace the SVGs to rebrand.
- `tests/test_app.py` — Playwright smoke test.

Data is cached for 10 minutes (`REFRESH_MIN` in `app.py`); editors/admins use
the sidebar "Refresh now" button to force a reload, or paste a Google Drive
loader URL to read from `Eurolink_Drive_Loader.gs` instead of the Sheet.
