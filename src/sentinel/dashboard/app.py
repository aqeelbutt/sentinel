"""Sentinel dashboard entry point. Run with: `streamlit run src/sentinel/dashboard/app.py`
or via the CLI: `sentinel dashboard`.
"""
from __future__ import annotations

import streamlit as st

from sentinel.dashboard.panels import (
    ai_analyst,
    audit,
    backtests,
    catalysts,
    movers,
    performance,
    portfolio,
    recommendations,
    sources,
    strategy_guide,
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

    # Visible ET clock + market-open indicator. Internal storage is UTC; all
    # times users see (here, on cards, on logs) are converted to America/New_York.
    from sentinel.core.clock import MarketClock as _MC
    _clock = _MC()
    _now_et = _clock.now()
    _is_open = _clock.is_market_open()
    _open_emoji = "🟢 OPEN" if _is_open else "🔴 CLOSED"
    st.sidebar.markdown(
        f"<div style='font-size:0.85rem; color:#94a3b8; margin-top:-0.3rem;'>"
        f"<b>{_now_et.strftime('%a %b %d · %I:%M:%S %p ET')}</b><br/>"
        f"NYSE: {_open_emoji}</div>",
        unsafe_allow_html=True,
    )

    acct = broker.account()
    st.sidebar.metric("Equity", f"${acct.equity:,.2f}")
    st.sidebar.metric("Cash", f"${acct.cash:,.2f}")
    st.sidebar.metric("Open positions", f"{acct.open_positions}/{cfg.risk.max_concurrent_positions}")
    st.sidebar.progress(min(1.0, acct.deployed_pct), text=f"Deployed {acct.deployed_pct:.0%}")

    # Button-based nav with active-state highlighting via session_state
    PAGES = [
        ("Recommendations", "📈"),
        ("AI Analyst", "🧠"),
        ("Market Pulse", "📊"),
        ("Catalyst Calendar", "📅"),
        ("Open positions", "💼"),
        ("Closed trades", "📜"),
        ("Performance", "📈"),
        ("Backtests", "🧪"),
        ("Watchlist", "👁️"),
        ("Data sources", "🔌"),
        ("Audit log", "🔍"),
        ("Strategy Guide", "📖"),
    ]
    if "current_page" not in st.session_state:
        st.session_state.current_page = "Recommendations"

    st.sidebar.markdown("**Navigate**")
    for name, icon in PAGES:
        is_active = st.session_state.current_page == name
        if st.sidebar.button(
            f"{icon}  {name}",
            key=f"nav_{name}",
            type="primary" if is_active else "secondary",
            width="stretch",
        ):
            st.session_state.current_page = name
            st.rerun()
    page = st.session_state.current_page

    # ---------- Quick Actions card ----------
    st.sidebar.divider()
    with st.sidebar.container(border=True):
        st.markdown("**⚡ Quick Actions**")
        if st.button("🔄 Run scan", key="qa_scan", width="stretch"):
            from sentinel.analytics.performance import snapshot_today
            from sentinel.core.clock import MarketClock
            from sentinel.strategy import scanner
            with st.spinner("Scanning…"):
                recs = scanner.run_scan(cfg, cfg.storage.db_path, MarketClock())
                snapshot_today(cfg.storage.db_path, starting_equity=cfg.app.initial_virtual_equity)
            st.toast(f"Scan done — {len(recs)} recs", icon="✅")
        if st.button("🧠 AI scan", key="qa_ai", width="stretch"):
            from sentinel.intelligence.claude_analyst import AIAnalystError, run_analyst
            with st.spinner("Claude analyzing…"):
                try:
                    ai_recs = run_analyst(cfg, cfg.storage.db_path)
                    st.toast(f"AI scan done — {len(ai_recs)} recs", icon="🧠")
                except AIAnalystError as e:
                    st.toast(f"AI scan failed: {e}", icon="❌")
        if st.button("📅 Refresh catalysts", key="qa_cat", width="stretch"):
            from sentinel.data.catalysts import refresh_calendar
            with st.spinner("Pulling catalysts…"):
                counts = refresh_calendar(cfg.storage.db_path, days_ahead=14)
            st.toast(f"Catalysts: {counts['earnings']} earnings, {counts['macro']} macro", icon="✅")
        if st.button("⚖️ Sweep positions", key="qa_sweep", width="stretch"):
            with st.spinner("Sweeping…"):
                closed = broker.sweep_open_positions()
            if closed:
                st.toast(f"Closed {len(closed)} positions", icon="✅")
            else:
                st.toast("No exits triggered", icon="ℹ️")

        # Inline ticker add
        new_sym = st.text_input("Add to watchlist", placeholder="e.g. AAPL", key="qa_addsym",
                                label_visibility="collapsed")
        if st.button("➕ Add ticker", key="qa_add", width="stretch") and new_sym:
            from sentinel.storage.repos import watchlist as _wl
            _wl.add(cfg.storage.db_path, new_sym)
            st.toast(f"Added {new_sym.upper()}", icon="✅")
            st.rerun()

    st.sidebar.divider()
    st.sidebar.caption(
        "Phase 1 — virtual portfolio only. No real broker connection. "
        "Profit-take and auto-stop run on every refresh."
    )

    # ---------- Top market-index banner ----------
    _render_index_banner(cfg.storage.db_path)

    if page == "Recommendations":
        recommendations.render()
    elif page == "AI Analyst":
        ai_analyst.render()
    elif page == "Market Pulse":
        movers.render()
    elif page == "Catalyst Calendar":
        catalysts.render()
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
    elif page == "Strategy Guide":
        strategy_guide.render()


_INDICES = [
    ("SPY",  "S&P 500"),
    ("QQQ",  "NASDAQ 100"),
    ("DIA",  "Dow Jones"),
    ("IWM",  "Russell 2000"),
    ("^VIX", "VIX"),
]

_PERIODS = {"1W": 7, "1M": 30, "3M": 90, "6M": 180, "1Y": 365}


def _render_index_banner(db_path) -> None:
    """Top-of-page market banner: current closes + Yahoo-style sparkline
    charts per index over a selectable period. Shows whether each closed up
    or down on the last trading day, plus the trajectory over time."""
    from sentinel.data.market import bar_store
    from sentinel.core.clock import MarketClock
    import plotly.graph_objects as go

    # ---------- top metric strip ----------
    cards: list[tuple[str, str, str, float | None, float | None]] = []
    for sym, label in _INDICES:
        bars = bar_store.get_bars(db_path, sym, "1d", days=5)
        if len(bars) < 2:
            cards.append((sym, label, "—", None, None))
            continue
        last = float(bars[-1].close)
        prev = float(bars[-2].close)
        pct = (last - prev) / prev * 100 if prev > 0 else 0.0
        cards.append((sym, label, f"{last:,.2f}", pct, last - prev))

    cols = st.columns(len(cards))
    for col, (sym, label, last_str, pct, dollar) in zip(cols, cards):
        if pct is None:
            with col:
                st.metric(label, "—", delta="data loading…")
            continue
        emoji = "🟢" if pct > 0 else ("🔴" if pct < 0 else "⚪")
        is_vix = "VIX" in label
        delta_color = "inverse" if is_vix else "normal"
        with col:
            st.metric(
                f"{emoji} {label}",
                last_str,
                delta=f"{pct:+.2f}%  (${dollar:+,.2f})",
                delta_color=delta_color,
            )

    # ---------- period selector + sparkline row ----------
    pcol1, pcol2 = st.columns([1, 6])
    with pcol1:
        period = st.selectbox(
            "Range", list(_PERIODS.keys()), index=1, key="banner_period",
            label_visibility="collapsed",
        )
    days = _PERIODS[period]

    chart_cols = st.columns(len(_INDICES))
    for col, (sym, label) in zip(chart_cols, _INDICES):
        bars = bar_store.get_bars(db_path, sym, "1d", days=days + 10)
        # Filter to exact requested window
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        in_window = [b for b in bars if b.ts >= cutoff]
        if len(in_window) < 2:
            with col:
                st.caption(f"{label}: not enough history")
            continue
        ys = [float(b.close) for b in in_window]
        xs = [b.ts.date() for b in in_window]
        period_change = (ys[-1] - ys[0]) / ys[0] * 100 if ys[0] > 0 else 0.0

        # Color logic: green if up, red if down. Invert for VIX.
        is_vix = "VIX" in label
        bullish = (period_change < 0) if is_vix else (period_change > 0)
        line_color = "#22c55e" if bullish else "#ef4444"
        fill_rgba = "rgba(34,197,94,0.18)" if bullish else "rgba(239,68,68,0.18)"

        # Tight y-range so the line uses the chart vertically
        y_min = min(ys) * 0.998
        y_max = max(ys) * 1.002

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=xs, y=ys,
            mode="lines",
            line=dict(color=line_color, width=2),
            fill="tozeroy",
            fillcolor=fill_rgba,
            hovertemplate="%{x|%b %d, %Y}<br>$%{y:,.2f}<extra></extra>",
        ))
        fig.update_layout(
            height=110,
            margin=dict(l=0, r=0, t=18, b=0),
            xaxis=dict(visible=False),
            yaxis=dict(visible=False, range=[y_min, y_max]),
            showlegend=False,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            title=dict(
                text=f"<span style='font-size:11px;color:#94a3b8;'>{label}: <b style='color:{line_color};'>{period_change:+.2f}%</b> over {period}</span>",
                x=0.0, y=0.98, xanchor="left", yanchor="top",
            ),
        )
        with col:
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    # Caption row
    clk = MarketClock()
    open_str = "🟢 NYSE OPEN" if clk.is_market_open() else "🔴 NYSE CLOSED"
    st.caption(
        f"{open_str}  ·  Daily closes from yfinance (cached). "
        f"Sparklines show last {days} days; VIX colors inverted (falling VIX = bullish for risk).  ·  "
        f"{clk.now().strftime('%a %b %d %I:%M:%S %p ET')}"
    )
    st.divider()


main()
