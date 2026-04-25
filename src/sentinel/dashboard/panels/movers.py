"""Market Pulse — top daily and weekly movers with news context.

Pulls % change from yfinance (cached) for the discovery universe + watchlist,
sorts by absolute move, surfaces the top N. For each mover, attaches recent
headlines from sentiment_cache so you can see WHY it moved.

Note: news context only appears for symbols Sentinel has scanned recently
(news fetchers are symbol-scoped via the watchlist+discovery scan loop).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import streamlit as st

from sentinel.dashboard.format import fmt_ts, money, pct
from sentinel.dashboard.state import get_config
from sentinel.data.market import bar_store
from sentinel.data.universe import DISCOVERY_UNIVERSE
from sentinel.storage.db import connect
from sentinel.storage.repos import watchlist


def render() -> None:
    cfg = get_config()
    st.title("📊 Market Pulse")
    st.caption("Top movers across Sentinel's universe + your watchlist. News context pulled from cached sentiment articles.")

    universe = sorted(set(DISCOVERY_UNIVERSE) | set(watchlist.list_all(cfg.storage.db_path)))

    # ---------- top tabs: Daily / Weekly ----------
    tab_d, tab_w = st.tabs(["📈 Daily movers", "📅 Weekly movers"])
    with tab_d:
        _render_movers_tab(cfg, universe, lookback_days=2, label="today")
    with tab_w:
        _render_movers_tab(cfg, universe, lookback_days=7, label="this week")


def _render_movers_tab(cfg, universe: list[str], lookback_days: int, label: str) -> None:
    progress = st.progress(0.0, text=f"Loading {len(universe)} symbols…")
    rows: list[dict] = []
    for i, sym in enumerate(universe):
        try:
            bars = bar_store.get_bars(cfg.storage.db_path, sym, "1d", days=max(lookback_days + 5, 10))
        except Exception:  # noqa: BLE001
            continue
        if len(bars) < 2:
            continue
        # Find first bar at-or-before (today - lookback) and the latest bar
        last = bars[-1]
        target_idx = None
        for j in range(len(bars) - 1, -1, -1):
            if bars[j].ts <= last.ts - timedelta(days=lookback_days):
                target_idx = j
                break
        if target_idx is None:
            target_idx = max(0, len(bars) - lookback_days - 1)
        prev = bars[target_idx]
        change = float((last.close - prev.close) / prev.close) if prev.close > 0 else 0.0
        rows.append({
            "symbol": sym,
            "from": float(prev.close),
            "to": float(last.close),
            "change_pct": change,
            "volume": int(last.volume),
            "as_of": last.ts.isoformat(),
        })
        progress.progress((i + 1) / len(universe))
    progress.empty()

    if not rows:
        st.info("Not enough cached price history. Run a scan first to populate.")
        return

    df = pd.DataFrame(rows).sort_values("change_pct", ascending=False)

    # ---------- Top gainers + losers metric strip ----------
    top_gainers = df.head(5).to_dict("records")
    top_losers = df.tail(5).to_dict("records")

    gcol, lcol = st.columns(2)
    with gcol:
        st.markdown(f"#### 🟢 Top 5 gainers ({label})")
        for r in top_gainers:
            st.metric(
                r["symbol"],
                value=money(r["to"]),
                delta=f"{r['change_pct']*100:+.2f}%",
            )
    with lcol:
        st.markdown(f"#### 🔴 Top 5 losers ({label})")
        for r in reversed(top_losers):
            st.metric(
                r["symbol"],
                value=money(r["to"]),
                delta=f"{r['change_pct']*100:+.2f}%",
            )

    # ---------- Top closers with news context ----------
    st.divider()
    st.subheader(f"🏆 Why these moved ({label})")
    st.caption("Recent headlines from cached news — sourced from Finnhub/NewsAPI/Yahoo Finance/SEC EDGAR.")

    top_n = st.slider("Show top N", 3, 15, 8, key=f"topn_{label}")
    for r in df.head(top_n).to_dict("records"):
        with st.container(border=True):
            h1, h2, h3 = st.columns([2, 2, 4])
            with h1:
                st.markdown(f"### {r['symbol']}")
            with h2:
                st.metric("Change", f"{r['change_pct']*100:+.2f}%",
                          delta=money(Decimal(str(r['to'])) - Decimal(str(r['from']))))
            with h3:
                st.write(f"**Now:** {money(r['to'])}  ·  **Was:** {money(r['from'])}  ·  **Vol:** {r['volume']:,}")

            # Pull recent news for this symbol from sentiment_cache
            articles = _fetch_recent_news(cfg.storage.db_path, r["symbol"], hours=lookback_days * 24)
            if articles:
                st.markdown("**News context:**")
                for art in articles[:5]:
                    sentiment_str = ""
                    if art["raw_score"] is not None:
                        s = art["raw_score"]
                        emoji = "🟢" if s > 0.2 else ("🔴" if s < -0.2 else "⚪")
                        sentiment_str = f"  {emoji} {s:+.2f}"
                    st.markdown(
                        f"- **[{art['source']}]** {art['title']}  "
                        f"_({fmt_ts(art['published_at'], with_seconds=False)})_{sentiment_str}"
                    )
            else:
                st.caption("_No cached news for this symbol yet — run a scan to ingest._")


def _fetch_recent_news(db_path, symbol: str, hours: int = 48) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT article_id, source, raw_score, published_at, title
            FROM sentiment_cache
            WHERE published_at >= ?
              AND symbols LIKE ?
            ORDER BY published_at DESC
            LIMIT 10
            """,
            (cutoff, f'%"{symbol}"%'),
        ).fetchall()
    return [dict(r) for r in rows]
