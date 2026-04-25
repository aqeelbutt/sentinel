"""Catalyst Calendar panel — week-ahead earnings + macro events.

Helps you avoid trading symbols with imminent earnings (RiskManager already
blocks them) and pre-stage hot names for the post-event session.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from sentinel.dashboard.format import fmt_ts
from sentinel.dashboard.state import get_config
from sentinel.data.catalysts import refresh_calendar
from sentinel.storage.repos import catalysts as catalysts_repo
from sentinel.storage.repos import positions, watchlist


def render() -> None:
    cfg = get_config()
    st.title("📅 Catalyst Calendar")
    st.caption(
        "Upcoming earnings, IPOs, and macro events. Symbols with earnings within "
        f"**{cfg.risk.earnings_blackout_hours:.0f}h** are blocked from new entries by RiskManager."
    )

    c1, c2, c3 = st.columns([1.5, 1.5, 5])
    with c1:
        if st.button("🔄 Refresh calendar", type="primary", width="stretch"):
            with st.spinner("Pulling earnings + IPO + macro events…"):
                counts = refresh_calendar(cfg.storage.db_path, days_ahead=14)
            st.success(f"Refreshed: {counts['earnings']} earnings, {counts['ipo']} IPOs, {counts['macro']} macro events")
            st.rerun()
    with c2:
        days_ahead = st.selectbox("Window", [3, 7, 14, 30], index=1)
    with c3:
        scope = st.radio(
            "Scope",
            ["watchlist + open positions", "everything"],
            horizontal=True,
            label_visibility="collapsed",
        )

    # Build symbol scope
    relevant_syms: set[str] = set()
    if scope == "watchlist + open positions":
        relevant_syms = set(watchlist.list_all(cfg.storage.db_path))
        relevant_syms |= {p["symbol"] for p in positions.list_open(cfg.storage.db_path)}

    events = catalysts_repo.list_for_dashboard(cfg.storage.db_path, days=days_ahead)
    if scope == "watchlist + open positions" and relevant_syms:
        events = [e for e in events if e["symbol"] in relevant_syms or e["catalyst_type"] == "macro"]

    if not events:
        st.info(
            "No catalysts cached yet. Click **Refresh calendar** above. "
            "(Requires Finnhub API key for earnings/IPO; macro events always work.)"
        )
        return

    # ---------- summary metrics ----------
    today = date.today()
    today_count = sum(1 for e in events if e["event_date"] == today.isoformat())
    tomorrow_count = sum(1 for e in events if e["event_date"] == (today + timedelta(days=1)).isoformat())
    earnings_count = sum(1 for e in events if e["catalyst_type"] == "earnings")
    macro_count = sum(1 for e in events if e["catalyst_type"] == "macro")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Today", today_count)
    m2.metric("Tomorrow", tomorrow_count)
    m3.metric("Earnings", earnings_count)
    m4.metric("Macro events", macro_count)

    st.divider()

    # ---------- grouped by day ----------
    open_pos_syms = {p["symbol"] for p in positions.list_open(cfg.storage.db_path)}
    by_date: dict[str, list[dict]] = {}
    for e in events:
        by_date.setdefault(e["event_date"], []).append(e)

    for d_str in sorted(by_date.keys()):
        d = date.fromisoformat(d_str)
        days_away = (d - today).days
        emoji = "🔴" if days_away == 0 else ("🟠" if days_away == 1 else ("🟡" if days_away <= 3 else "🟢"))
        weekday = d.strftime("%A")
        with st.expander(f"{emoji}  **{weekday}, {d.isoformat()}**  ({len(by_date[d_str])} events, {days_away}d away)",
                         expanded=(days_away <= 3)):
            rows = []
            for e in by_date[d_str]:
                in_pos = "🛑 OPEN POSITION — RISK" if e["symbol"] in open_pos_syms else ""
                blackout = ""
                if e["catalyst_type"] == "earnings":
                    try:
                        evt_dt = datetime.fromisoformat(e["event_dt_iso"])
                        hours_until = (evt_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                        if 0 < hours_until <= cfg.risk.earnings_blackout_hours:
                            blackout = f"⛔ blackout (in {hours_until:.0f}h)"
                    except (KeyError, ValueError):
                        pass
                rows.append({
                    "Symbol": e["symbol"],
                    "Type": e["catalyst_type"],
                    "When": (e["event_time"] or "—").upper(),
                    "Title": e["title"] or "—",
                    "Status": " ".join(filter(None, [in_pos, blackout])),
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
