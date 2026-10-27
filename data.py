"""Data loading and computation for the Eurolink claims dashboard.

Ported from the client-side logic in Eurolink_Claims_Live.html: fetch the
claims extract (Google Sheet, Google Drive loader, or an uploaded file),
classify each row as IP / OPD / HEMO, and compute the summary tables the
dashboard charts are built from.
"""
from __future__ import annotations

import base64
import gzip
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests

SHEET_ID = "1QP2Z2JIKqJSlryILi0LYh4kjbx255nC9aIk6Okl--5c"
SOURCES = [{"sheet": "all"}, {"gid": "820586061"}, {"sheet": "HEMODIALYSIS"}]

FILING_DAYS = 60
RECENT_DAYS = 90

NEED = ["Id", "Discharge Date", "First Case Rate", "Date Submitted", "Patient Type", "Status"]

TYPES = ["IP", "OPD", "HEMO"]

STATUS_GROUPS = {
    "Paid (with cheque/voucher)": {"WITH CHEQUE", "WITH VOUCHER"},
    "In process / refiled": {"IN PROCESS", "REFILED"},
    "Returned / denied / error": {
        "RETURN", "DENIED", "FAILED CHECKING", "UPDATE ERROR", "CLAIM SERIES NOT FOUND",
    },
    "Unprocessed": {"UNPROCESSED"},
}

BUCKET_LABELS = ["≤30 d", "31–45 d", "46–55 d", "56–60 d", "61–90 d", ">90 d"]
BUCKET_EDGES = [30, 45, 55, 60, 90, 10**9]


class SourceError(Exception):
    """Raised when none of the configured sources returned usable data."""

    def __init__(self, tried: list[str]):
        super().__init__("; ".join(tried))
        self.tried = tried


def _sheet_url(source: dict) -> str:
    base = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?tqx=out:csv&"
    if "sheet" in source:
        return base + f"sheet={requests.utils.quote(source['sheet'])}"
    return base + f"gid={source['gid']}"


def _label(source: dict) -> str:
    return f'tab "{source["sheet"]}"' if "sheet" in source else f'gid {source["gid"]}'


def _http_text(url: str, timeout: int = 30) -> str:
    resp = requests.get(url, timeout=timeout, headers={"Cache-Control": "no-store"})
    resp.raise_for_status()
    text = resp.text
    if re.match(r"^\s*<(!doctype|html)", text, re.I):
        raise ValueError("sign-in page returned")
    return text


def _has_all(text: str) -> bool:
    header_line = text.splitlines()[0] if text else ""
    cols = list(pd.read_csv(io.StringIO(header_line), nrows=0).columns)
    return all(n in cols for n in NEED)


def fetch_drive(drive_url: str, timeout: int = 60) -> tuple[str, str]:
    resp = requests.get(drive_url, params={"_": str(int(datetime.now().timestamp() * 1000))}, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("error"):
        raise ValueError(payload["error"])
    raw = gzip.decompress(base64.b64decode(payload["gz"]))
    text = raw.decode("utf-8")
    if not _has_all(text):
        raise ValueError("unexpected columns")
    names = " + ".join(f["name"] for f in payload.get("files", []))
    return text, f"Google Drive ({names})"


def fetch_sheet(drive_url: Optional[str] = None) -> tuple[str, str]:
    tried: list[str] = []
    if drive_url:
        try:
            return fetch_drive(drive_url)
        except Exception as e:  # noqa: BLE001
            tried.append(f"Drive: {e}")
    for source in SOURCES:
        label = _label(source)
        try:
            text = _http_text(_sheet_url(source))
            if _has_all(text):
                return text, label
            tried.append(f"{label}: columns not found")
        except Exception as e:  # noqa: BLE001
            tried.append(f"{label}: {e}")
    raise SourceError(tried)


def rows_from_text(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    missing = [n for n in NEED if n not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")
    return df[NEED]


def rows_from_file(file_name: str, file_bytes: bytes) -> tuple[pd.DataFrame, str]:
    if file_name.lower().endswith(".csv"):
        df = rows_from_text(file_bytes.decode("utf-8", errors="replace"))
        return df, f"file {file_name}"
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    order = (
        [n for n in xls.sheet_names if n.lower() == "all"]
        + [n for n in xls.sheet_names if n.lower() == "hemodialysis"]
        + list(xls.sheet_names)
    )
    seen = set()
    for name in order:
        if name in seen:
            continue
        seen.add(name)
        raw = xls.parse(name, header=0, dtype=str, keep_default_na=False)
        raw.columns = [str(c).strip() for c in raw.columns]
        if all(n in raw.columns for n in NEED):
            return raw[NEED], f'{file_name}, tab "{name}"'
    raise ValueError("No tab in this workbook has the claims columns.")


def _to_date(value: str) -> Optional[datetime]:
    if value is None or value == "":
        return None
    m = re.match(
        r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:[ T]+(\d{1,2}):(\d{2})(?::\d{2})?\s*([AaPp][Mm])?)?",
        value.strip(),
    )
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour = int(m.group(4) or 0)
        ap = (m.group(6) or "").upper()
        if ap == "PM" and hour < 12:
            hour += 12
        if ap == "AM" and hour == 12:
            hour = 0
        minute = int(m.group(5) or 0)
        try:
            return datetime(year, month, day, hour, minute)
        except ValueError:
            return None
    try:
        return pd.to_datetime(value).to_pydatetime()
    except Exception:  # noqa: BLE001
        return None


def _classify(first_case_rate: str, patient_type: str) -> Optional[str]:
    m = re.search(r"RVS:\s*(\d+)", first_case_rate or "")
    if m and m.group(1) in ("90935", "90937"):
        return "HEMO"
    pt = (patient_type or "").strip()
    if pt == "Inpatient":
        return "IP"
    if pt == "Outpatient":
        return "OPD"
    return None


def _round1(v) -> str:
    return "" if v is None or pd.isna(v) else str(round(float(v) * 10) / 10)


def _month_start(d: datetime) -> datetime:
    return datetime(d.year, d.month, 1)


def _add_months(d: datetime, n: int) -> datetime:
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    return datetime(year, month, 1)


def _days_in_month(d: datetime) -> int:
    return (_add_months(d, 1) - timedelta(days=1)).day


@dataclass
class Claims:
    meta: dict
    summary: pd.DataFrame
    status: pd.DataFrame
    buckets: pd.DataFrame
    monthly: pd.DataFrame
    daily: pd.DataFrame
    calendar: list = field(default_factory=list)


def compute(raw: pd.DataFrame, range_months: int = 24) -> Claims:
    recs = []
    seen_ids: set[str] = set()
    cols = zip(
        raw["Id"].tolist(),
        raw["First Case Rate"].tolist(),
        raw["Patient Type"].tolist(),
        raw["Discharge Date"].tolist(),
        raw["Date Submitted"].tolist(),
        raw["Status"].tolist(),
    )
    for rid_raw, fcr, pt, dis_raw, sub_raw, status_raw in cols:
        rid = str(rid_raw or "").strip()
        if rid:
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
        rtype = _classify(fcr, pt)
        if not rtype:
            continue
        dis = _to_date(dis_raw)
        sub = _to_date(sub_raw)
        delay = (sub - dis).days if (dis and sub) else None
        recs.append(
            {
                "type": rtype,
                "status": (status_raw or "").strip().upper(),
                "sub": sub,
                "delay": delay if (delay is not None and delay >= 0) else None,
                "valid": bool(sub) and delay is not None and delay >= 0,
            }
        )
    if not recs:
        raise ValueError("No IP, OPD or HEMO claims were found in the data.")
    recs = pd.DataFrame(recs)
    submitted = recs[recs["valid"]]
    if submitted.empty:
        raise ValueError("No claims with a submission date were found.")

    max_sub = submitted["sub"].max()
    recent_from = max_sub - timedelta(days=RECENT_DAYS)

    # ---------- summary ----------
    summary_rows = []
    for t in TYPES:
        all_t = recs[recs["type"] == t]
        s = submitted[submitted["type"] == t]
        sc = s[s["delay"] <= 180]
        rc = s[s["sub"] >= recent_from]
        rcc = rc[rc["delay"] <= 180]
        late = s[s["delay"] > FILING_DAYS + 0.5]
        late_rc = rc[rc["delay"] > FILING_DAYS + 0.5]
        summary_rows.append(
            {
                "type": t,
                "claims": len(all_t),
                "submitted": len(s),
                "avg_delay": _round1(sc["delay"].mean()) if len(sc) else "",
                "median_delay": _round1(s["delay"].median()) if len(s) else "",
                "late_pct": _round1(100 * len(late) / len(s)) if len(s) else "0",
                "per_day_recent": _round1(len(rc) / RECENT_DAYS),
                "avg_delay_recent": _round1(rcc["delay"].mean()) if len(rcc) else "",
                "late_pct_recent": _round1(100 * len(late_rc) / len(rc)) if len(rc) else "0",
            }
        )
    summary = pd.DataFrame(summary_rows)

    # ---------- status ----------
    status_rows = []
    for t in TYPES:
        for group, statuses in STATUS_GROUPS.items():
            n = len(recs[(recs["type"] == t) & (recs["status"].isin(statuses))])
            status_rows.append({"type": t, "group": group, "claims": n})
    status = pd.DataFrame(status_rows)

    # ---------- buckets ----------
    bucket_rows = []
    for i, label in enumerate(BUCKET_LABELS):
        lo = BUCKET_EDGES[i - 1] if i else -1
        hi = BUCKET_EDGES[i]
        for t in TYPES:
            s = submitted[submitted["type"] == t]
            n = len(s[(s["delay"] > lo) & (s["delay"] <= hi)])
            bucket_rows.append({"bucket": label, "type": t, "pct": _round1(100 * n / len(s)) if len(s) else "0"})
    buckets = pd.DataFrame(bucket_rows)

    # ---------- monthly ----------
    last = _month_start(max_sub)
    first = _month_start(submitted["sub"].min())
    if range_months:
        floor = _add_months(last, -(range_months - 1))
        first = max(first, floor)
    monthly_rows = []
    months = []
    cur = first
    while cur <= last:
        months.append(cur)
        cur = _add_months(cur, 1)
    last_key = _month_start(max_sub)
    for month in months:
        key = month.strftime("%Y-%m")
        in_month = submitted[(submitted["sub"] >= month) & (submitted["sub"] < _add_months(month, 1))]
        days = max_sub.day if month == last_key else _days_in_month(month)
        for t in TYPES:
            a = in_month[in_month["type"] == t]
            c = a[a["delay"] <= 180]
            late = a[a["delay"] > FILING_DAYS + 0.5]
            monthly_rows.append(
                {
                    "month": key + "-01",
                    "type": t,
                    "submitted": len(a),
                    "per_day": _round1(len(a) / days),
                    "avg_delay": _round1(c["delay"].mean()) if len(c) else "",
                    "late_pct": _round1(100 * len(late) / len(a)) if len(a) else "",
                }
            )
    monthly = pd.DataFrame(monthly_rows)

    # ---------- daily (trailing 12 months) and calendar ----------
    start = _add_months(_month_start(max_sub), -11)
    end = datetime(max_sub.year, max_sub.month, max_sub.day)
    month_totals = submitted.groupby(submitted["sub"].apply(lambda d: d.strftime("%Y-%m"))).size()
    submitted = submitted.copy()
    submitted["day_key"] = submitted["sub"].apply(lambda d: d.strftime("%Y-%m-%d"))
    recent = submitted[submitted["sub"] >= start]
    by_day = recent.groupby(["day_key", "type"]).size().unstack(fill_value=0)

    daily_rows = []
    day = start
    while day <= end:
        key = day.strftime("%Y-%m-%d")
        mkey = day.strftime("%Y-%m")
        active = month_totals.get(mkey, 0) >= 10
        for t in TYPES:
            n = ""
            if active:
                n = int(by_day.loc[key, t]) if key in by_day.index and t in by_day.columns else 0
            daily_rows.append({"day": key, "type": t, "claims": n})
        day += timedelta(days=1)
    daily = pd.DataFrame(daily_rows)

    calendar = []
    for i in range(12):
        ms = _add_months(start, i)
        key = ms.strftime("%Y-%m")
        dim = _days_in_month(ms)
        days = []
        tot = {t: 0 for t in TYPES}
        for d in range(1, dim + 1):
            dt = datetime(ms.year, ms.month, d)
            dkey = dt.strftime("%Y-%m-%d")
            counts = {t: int(by_day.loc[dkey, t]) if dkey in by_day.index and t in by_day.columns else 0 for t in TYPES}
            fut = dt > end
            days.append({"d": d, "k": dkey, "wd": (dt.weekday() + 1) % 7, "fut": fut, **counts, "t": sum(counts.values())})
            if not fut:
                for t in TYPES:
                    tot[t] += counts[t]
        calendar.append(
            {
                "key": key,
                "date": ms,
                "wd0": (ms.weekday() + 1) % 7,
                "tot": tot,
                "days": days,
                "active": month_totals.get(key, 0) >= 10 or i == 11,
            }
        )

    meta = {"claims": len(recs), "submitted": len(submitted), "latest": max_sub}
    return Claims(meta=meta, summary=summary, status=status, buckets=buckets, monthly=monthly, daily=daily, calendar=calendar)
