"""Browser smoke test for the Flask app, driven with Playwright.

Launches `python app.py` on a scratch port, drives it with a real
(headless) browser through the full login flow -- default admin login,
forced first-login password change, dashboard render, admin panel -- and
checks the page a user would actually see. Reuses the system Edge install
via executable_path so no extra ~300MB Chromium download is needed.

Run with: python tests/test_app.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8599
URL = f"http://localhost:{PORT}"
URL_PREFIX = "/lariosa/eclaims-report"
APP_URL = URL + URL_PREFIX
EDGE_PATHS = [
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
]

DEFAULT_USERNAME = "jessontan"
DEFAULT_PASSWORD = "ChangeMe123!"
NEW_PASSWORD = "TestPassw0rd!"


def _browser_path() -> str | None:
    for p in EDGE_PATHS:
        if Path(p).exists():
            return p
    return None


def _wait_ready(timeout: int = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(URL, timeout=2).status_code in (200, 302):
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError("Flask app did not come up in time")


def main() -> None:
    db_path = ROOT / "eurolink.db"
    had_db = db_path.exists()
    backup = db_path.read_bytes() if had_db else None
    if had_db:
        db_path.unlink()  # start from a clean seeded admin account

    env = dict(os.environ)
    env["FLASK_RUN_PORT"] = str(PORT)
    proc = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_ready()

        browser_path = _browser_path()
        with sync_playwright() as p:
            launch_kwargs = {"headless": True}
            if browser_path:
                launch_kwargs["executable_path"] = browser_path
            browser = p.chromium.launch(**launch_kwargs)
            page = browser.new_page(viewport={"width": 1400, "height": 900})

            page_errors = []
            page.on("pageerror", lambda e: page_errors.append(str(e)))

            # ---- 1. Login screen ----
            page.goto(APP_URL, wait_until="networkidle", timeout=30000)
            page.wait_for_selector("text=Eurolink claims submission", timeout=10000)
            login_btn = page.locator('button:has-text("Log in")')
            login_btn.wait_for(state="visible", timeout=15000)
            body = page.inner_text("body")
            assert "Sign in to continue." in body, "login gate text missing"
            page.screenshot(path=str(ROOT / "tests" / "_1_login.png"))

            # ---- 2. Log in with default admin credentials ----
            page.get_by_label("Username").fill(DEFAULT_USERNAME)
            page.get_by_label("Password", exact=True).fill(DEFAULT_PASSWORD)
            login_btn.click()
            page.wait_for_load_state("networkidle")

            # ---- 3. Forced password change ----
            page.wait_for_selector("text=You must set a new password", timeout=10000)
            page.screenshot(path=str(ROOT / "tests" / "_2_force_change.png"))
            page.get_by_label("New password", exact=True).fill(NEW_PASSWORD)
            page.get_by_label("Confirm new password").fill(NEW_PASSWORD)
            page.locator('button:has-text("Set new password")').click()
            page.wait_for_load_state("networkidle")

            # Changing the password doesn't force a fresh login -- the session
            # cookie stays authenticated and should drop straight into the dashboard.
            page.wait_for_selector("text=Signed in as", timeout=15000)
            page.wait_for_selector("text=User management", timeout=60000)
            body = page.inner_text("body")
            assert "role:" in body and "admin" in body, "role indicator missing"
            assert "Jesson Tan" in body, "signed-in display name not shown"
            assert "Data source" in body, "editor/admin sidebar controls missing"
            page.screenshot(path=str(ROOT / "tests" / "_3_dashboard.png"))

            # ---- 4. Log out, then confirm the new password actually works ----
            page.locator('button:has-text("Log out")').first.click()
            page.wait_for_selector("text=Sign in to continue.", timeout=10000)
            page.get_by_label("Username").fill(DEFAULT_USERNAME)
            page.get_by_label("Password", exact=True).fill(NEW_PASSWORD)
            page.locator('button:has-text("Log in")').click()
            page.wait_for_load_state("networkidle")
            body = page.inner_text("body")
            assert "You must set a new password" not in body, "asked to change password again after it was already changed"

            page.wait_for_selector("text=Signed in as", timeout=15000)
            page.wait_for_selector("text=User management", timeout=60000)

            # ---- 5. Audit log panel is present and populated (collapsed by default --
            # click its header to expand, same as a real admin would) ----
            page.wait_for_selector("text=Audit log", timeout=10000)
            page.locator('summary:has-text("Audit log")').click()
            page.wait_for_selector("text=Filter by action", timeout=10000)
            body = page.inner_text("body")
            assert "login_success" in body, "audit log table does not show login_success rows"
            page.screenshot(path=str(ROOT / "tests" / "_4_audit_log.png"))

            browser.close()
            assert not page_errors, f"JS errors on page: {page_errors}"

            # ---- 6. Verify the audit trail directly in the database ----
            sys.path.insert(0, str(ROOT))
            import db as _db  # noqa: E402
            actions = [r["action"] for r in _db.recent_audit(limit=50)]
            assert "login_success" in actions, f"no login_success in audit_log: {actions}"
            assert "password_changed" in actions, f"no password_changed in audit_log: {actions}"
            assert actions.count("login_success") >= 2, "expected two login_success entries (initial + re-login)"

            print("Login screen OK")
            print("Default admin login OK")
            print("Forced password change OK")
            print("Re-login with new password OK (not asked to change again)")
            print("Dashboard rendered behind the gate OK")
            print("Admin panel visible OK")
            print("Audit log panel visible and populated OK")
            print("Audit log recorded login/password events OK (verified in DB)")
            print("No uncaught JS errors OK")
            print("PASS")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if backup is not None:
            db_path.write_bytes(backup)
        elif db_path.exists():
            db_path.unlink()


if __name__ == "__main__":
    main()
