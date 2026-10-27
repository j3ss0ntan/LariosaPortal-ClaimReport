"""Plotly chart builders for the Eurolink claims dashboard."""
from __future__ import annotations

import calendar as calmod
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

TYPES = ["IP", "OPD", "HEMO"]
TYPE_LABEL = {"IP": "Inpatient (IP)", "OPD": "Outpatient (OPD)", "HEMO": "Hemodialysis (HEMO)"}
COLORS = {"IP": "#2a78d6", "OPD": "#eb6834", "HEMO": "#1baf7a"}
STATUS_COLORS = ["#c98500", "#e87ba4", "#e34948", "#9085e9"]


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def line_chart(df: pd.DataFrame, x_col: str, y_col: str, *, title_unit: str = "", ref: float | None = None, y_max: float | None = None) -> go.Figure:
    fig = go.Figure()
    for t in TYPES:
        sub = df[df["type"] == t].copy()
        sub["_x"] = pd.to_datetime(sub[x_col])
        sub["_y"] = _num(sub, y_col)
        sub = sub.sort_values("_x")
        fig.add_trace(
            go.Scatter(
                x=sub["_x"], y=sub["_y"], mode="lines+markers", name=TYPE_LABEL[t],
                line=dict(color=COLORS[t], width=2), marker=dict(size=5),
                connectgaps=False,
            )
        )
    if ref is not None:
        fig.add_hline(y=ref, line_dash="dash", line_color="#8a919e")
    fig.update_layout(
        height=300, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.15),
        yaxis=dict(rangemode="tozero", range=[0, y_max] if y_max else None, ticksuffix=title_unit),
        hovermode="x unified",
    )
    return fig


def compare_chart(monthly: pd.DataFrame) -> go.Figure:
    months = sorted(monthly["month"].unique())
    cur, prev = months[-12:], months[-24:-12]
    fig = go.Figure()
    for i, t in enumerate(TYPES):
        sub = monthly[monthly["type"] == t].set_index("month")["submitted"].apply(lambda v: pd.to_numeric(v, errors="coerce"))
        cur_v = [sub.get(m) for m in cur]
        prev_v = [sub.get(m) for m in prev] if prev else [None] * 12
        prev_v = [None] * (12 - len(prev_v)) + prev_v
        labels = [datetime.strptime(m, "%Y-%m-%d").strftime("%b") for m in cur]
        fig.add_trace(go.Scatter(x=labels, y=prev_v, mode="lines", name=f"{t} (prior year)", line=dict(color=COLORS[t], width=2, dash="dash"), opacity=0.5, legendgroup=t, xaxis=f"x{i+1}", yaxis=f"y{i+1}", showlegend=False))
        fig.add_trace(go.Scatter(x=labels, y=cur_v, mode="lines+markers", name=TYPE_LABEL[t], line=dict(color=COLORS[t], width=2), legendgroup=t, xaxis=f"x{i+1}", yaxis=f"y{i+1}"))
    fig.update_layout(
        grid=dict(rows=1, columns=3, pattern="independent"),
        height=260, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.2),
    )
    return fig


def bucket_chart(buckets: pd.DataFrame) -> go.Figure:
    fig = px.bar(
        buckets, x="bucket", y=_num(buckets, "pct"), color="type", barmode="group",
        color_discrete_map=COLORS, labels={"y": "% of claims", "bucket": ""},
    )
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.15), yaxis_ticksuffix="%")
    return fig


def status_chart(status: pd.DataFrame) -> go.Figure:
    groups = list(status["group"].unique())
    fig = go.Figure()
    for i, g in enumerate(groups):
        sub = status[status["group"] == g]
        totals = status.groupby("type")["claims"].transform("sum")
        pct = status["claims"] / totals.replace(0, 1)
        fig.add_trace(
            go.Bar(
                y=sub["type"], x=pct[status["group"] == g], orientation="h", name=g,
                marker_color=STATUS_COLORS[i % len(STATUS_COLORS)],
                customdata=sub["claims"],
                hovertemplate="%{y} · " + g + "<br>%{customdata} claims (%{x:.1%})<extra></extra>",
            )
        )
    fig.update_layout(barmode="stack", height=200, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.3), xaxis_tickformat=".0%")
    return fig


def calendar_heatmap(month: dict) -> go.Figure:
    days = month["days"]
    dim = len(days)
    first_wd = month["wd0"]
    rows = (first_wd + dim + 6) // 7
    z = [[None] * 7 for _ in range(rows)]
    labels = [[""] * 7 for _ in range(rows)]
    hover = [[""] * 7 for _ in range(rows)]
    for d in days:
        pos = first_wd + d["d"] - 1
        r, c = divmod(pos, 7)
        if d["fut"]:
            continue
        z[r][c] = d["t"]
        labels[r][c] = str(d["d"])
        hover[r][c] = f"Day {d['d']}<br>IP {d['IP']} · OPD {d['OPD']} · HEMO {d['HEMO']}<br>Total {d['t']}"
    fig = go.Figure(
        go.Heatmap(
            z=z, text=labels, customdata=hover, texttemplate="%{text}",
            textfont=dict(size=10, color="#5d6574"),
            hovertemplate="%{customdata}<extra></extra>", colorscale="Blues",
            showscale=False, xgap=3, ygap=3, zmin=0,
        )
    )
    fig.update_yaxes(autorange="reversed", visible=False)
    fig.update_xaxes(
        visible=True, side="top", tickmode="array", tickvals=list(range(7)),
        ticktext=["S", "M", "T", "W", "T", "F", "S"], showgrid=False, zeroline=False,
    )
    fig.update_layout(height=30 * rows + 30, margin=dict(l=0, r=0, t=20, b=0))
    return fig
