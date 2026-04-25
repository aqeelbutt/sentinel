"""Performance — equity curve and per-window metrics versus SPY buy-and-hold."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from sentinel.analytics.performance import compute_window_metrics, snapshot_today
from sentinel.dashboard.format import money, pct
from sentinel.dashboard.state import get_config
from sentinel.storage.repos import equity_snapshots


def render() -> None:
    cfg = get_config()

    st.title("Performance")
    st.caption(
        "Strategy vs **SPY buy-and-hold** over rolling windows. "
        "After 30+ trades the verdict (BEATS / TIES / LOSES) becomes meaningful."
    )

    if st.button("📸 Take snapshot of today's equity"):
        snapshot_today(cfg.storage.db_path, starting_equity=cfg.app.initial_virtual_equity)
        st.success("Snapshot saved.")

    snaps = equity_snapshots.list_all(cfg.storage.db_path)
    if not snaps:
        st.warning(
            "No equity snapshots yet. Run `sentinel scan` once (or click the snapshot button) "
            "and the equity curve will start populating."
        )
        return

    df = pd.DataFrame([
        {"date": s["snapshot_date"], "equity": float(s["equity"]),
         "spy": float(s["spy_close"]) if s["spy_close"] else None}
        for s in snaps
    ])

    # Normalize both series to 100 at first date so the chart is apples-to-apples
    df = df.sort_values("date").reset_index(drop=True)
    df["equity_norm"] = df["equity"] / df["equity"].iloc[0] * 100
    if df["spy"].notna().any():
        first_spy = df["spy"].dropna().iloc[0]
        df["spy_norm"] = df["spy"] / first_spy * 100
    else:
        df["spy_norm"] = None

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["date"], y=df["equity_norm"],
                             mode="lines+markers", name="Sentinel virtual"))
    if df["spy_norm"].notna().any():
        fig.add_trace(go.Scatter(x=df["date"], y=df["spy_norm"],
                                 mode="lines", name="SPY buy-and-hold"))
    fig.update_layout(
        height=400,
        xaxis_title="date",
        yaxis_title="indexed equity (start = 100)",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig, width="stretch")

    st.divider()
    st.subheader("Per-window metrics")

    windows = ["1w", "2w", "3w", "1mo", "3mo", "6mo", "1y", "inception"]
    rows = []
    for w in windows:
        m = compute_window_metrics(cfg.storage.db_path, w)
        if m is None:
            rows.append({"Window": w, "Status": "no data"})
            continue
        rows.append({
            "Window": w,
            "Days": m.days,
            "Trades": m.trades_in_window,
            "Total return": pct(m.portfolio_total_return),
            "Sharpe": f"{m.portfolio_sharpe:.2f}",
            "Sortino": f"{m.portfolio_sortino:.2f}",
            "Max DD": pct(m.portfolio_max_drawdown),
            "Win rate": pct(m.portfolio_win_rate) if m.portfolio_win_rate is not None else "—",
            "SPY return": pct(m.spy_total_return),
            "SPY Sharpe": f"{m.spy_sharpe:.2f}",
            "Alpha vs SPY": pct(m.alpha_vs_spy),
            "Verdict": m.verdict,
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.caption(
        "Verdict legend: **BEATS_SPY** = strategy Sharpe > SPY Sharpe + 0.2; "
        "**LOSES_TO_SPY** = strategy Sharpe < SPY Sharpe - 0.2; "
        "**TIES_SPY** = within ±0.2; **INCONCLUSIVE** = fewer than 10 trades in window."
    )
