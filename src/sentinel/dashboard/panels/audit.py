"""Audit log — every decision the system has made (filterable)."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from sentinel.dashboard.format import fmt_ts
from sentinel.dashboard.state import get_config
from sentinel.storage.repos import decisions


def render() -> None:
    cfg = get_config()
    st.title("Audit log")
    st.caption("Append-only decisions log. Every signal evaluation, sentiment score, regime check, risk decision, fill, and close.")

    col1, col2, col3 = st.columns(3)
    with col1:
        kind = st.selectbox(
            "Filter by kind",
            options=["", "signal", "sentiment", "regime", "risk", "order", "fill", "close"],
        )
    with col2:
        symbol = st.text_input("Filter by symbol (optional)")
    with col3:
        limit = st.slider("Limit", min_value=20, max_value=1000, value=200, step=20)

    rows = decisions.list_recent(
        cfg.storage.db_path,
        limit=limit,
        kind=kind or None,
        symbol=symbol.upper() if symbol else None,
    )
    if not rows:
        st.info("No matching decisions.")
        return

    table = []
    for r in rows:
        try:
            payload = json.loads(r["payload"])
            payload_str = json.dumps(payload, indent=None)[:200]
        except json.JSONDecodeError:
            payload_str = r["payload"][:200]
        table.append({
            "Time": fmt_ts(r["ts"]),
            "Kind": r["kind"],
            "Symbol": r["symbol"] or "—",
            "Payload (truncated)": payload_str,
        })
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
