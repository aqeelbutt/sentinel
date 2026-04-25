"""Sentinel dashboard entry point. Run with: `streamlit run src/sentinel/dashboard/app.py`
or via the CLI: `sentinel dashboard`.
"""
from __future__ import annotations

import streamlit as st

from sentinel.dashboard.panels import (
    audit,
    backtests,
    movers,
    performance,
    portfolio,
    recommendations,
    sources,
    trades,
    watchlist,
)
from sentinel.dashboard.state import get_broker, get_config

st.set_page_config(
    page_title="Sentinel",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS — gradient logo header, more visible buttons, card-style containers.
# Streamlit's theme covers colors; this layer adds gradients + button polish.
_CUSTOM_CSS = """
<style>
  /* Gradient logo */
  .sentinel-logo {
    background: linear-gradient(90deg, #06b6d4 0%, #8b5cf6 50%, #ec4899 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    font-weight: 800;
    font-size: 2rem;
    letter-spacing: -0.02em;
    margin: 0;
    padding: 0;
  }
  .sentinel-tagline {
    color: #94a3b8;
    font-size: 0.85rem;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    margin-top: -0.25rem;
  }
  /* Make primary buttons stand out: gradient + glow */
  .stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #06b6d4 0%, #8b5cf6 100%);
    border: none;
    color: white;
    font-weight: 600;
    box-shadow: 0 4px 14px 0 rgba(6, 182, 212, 0.3);
    transition: transform 0.1s, box-shadow 0.2s;
  }
  .stButton > button[kind="primary"]:hover {
    transform: translateY(-1px);
    box-shadow: 0 6px 20px 0 rgba(6, 182, 212, 0.45);
  }
  /* Cards (st.container with border=True) get a soft glow on hover */
  div[data-testid="stVerticalBlockBorderWrapper"] {
    transition: border-color 0.2s;
  }
  div[data-testid="stVerticalBlockBorderWrapper"]:hover {
    border-color: #06b6d4 !important;
  }
  /* Metrics nicer spacing */
  div[data-testid="stMetric"] {
    background: rgba(21, 32, 51, 0.5);
    padding: 0.5rem 0.75rem;
    border-radius: 8px;
    border: 1px solid rgba(148, 163, 184, 0.1);
  }
  /* Sidebar polish */
  section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0b1220 0%, #0f172a 100%);
  }
</style>
"""


def main() -> None:
    st.markdown(_CUSTOM_CSS, unsafe_allow_html=True)
    cfg = get_config()
    broker = get_broker()

    # On every refresh: sweep open positions for profit-take / hard-stop hits.
    # Cheap when there are 0–5 positions; runs without user action.
    auto_closed = broker.sweep_open_positions()
    if auto_closed:
        st.toast(f"Auto-closed {len(auto_closed)} position(s) at profit-take or stop", icon="✅")

    # Branded logo header in the sidebar
    st.sidebar.markdown(
        """
        <div style="padding: 0.5rem 0 1rem 0;">
          <div class="sentinel-logo">⬢ SENTINEL</div>
          <div class="sentinel-tagline">Triple-Check Trading Engine</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.sidebar.caption(f"Mode: **{cfg.app.mode.upper()}** · No broker connected")

    acct = broker.account()
    st.sidebar.metric("Equity", f"${acct.equity:,.2f}")
    st.sidebar.metric("Cash", f"${acct.cash:,.2f}")
    st.sidebar.metric("Open positions", f"{acct.open_positions}/{cfg.risk.max_concurrent_positions}")
    st.sidebar.progress(min(1.0, acct.deployed_pct), text=f"Deployed {acct.deployed_pct:.0%}")

    page = st.sidebar.radio(
        "View",
        options=[
            "Recommendations",
            "Market Pulse",
            "Open positions",
            "Closed trades",
            "Performance",
            "Backtests",
            "Watchlist",
            "Data sources",
            "Audit log",
        ],
        index=0,
    )

    st.sidebar.divider()
    st.sidebar.caption(
        "Phase 1 — virtual portfolio only. No real broker connection. "
        "Profit-take and auto-stop run on every refresh."
    )

    if page == "Recommendations":
        recommendations.render()
    elif page == "Market Pulse":
        movers.render()
    elif page == "Open positions":
        portfolio.render()
    elif page == "Closed trades":
        trades.render()
    elif page == "Performance":
        performance.render()
    elif page == "Backtests":
        backtests.render()
    elif page == "Watchlist":
        watchlist.render()
    elif page == "Data sources":
        sources.render()
    elif page == "Audit log":
        audit.render()


main()
