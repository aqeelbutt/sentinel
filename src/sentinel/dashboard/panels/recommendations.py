"""Recommendations — card-based view. One card per rec with full context,
signals as badges, forecast stats, expand for the audit trail.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import streamlit as st

from sentinel.core.types import OrderIntent, Side
from sentinel.dashboard.format import fmt_ts, money, pct
from sentinel.dashboard.state import get_broker, get_clock, get_config, get_risk_manager
from sentinel.risk.manager import compute_equity_state
from sentinel.storage.repos import backtest_trades, recommendations
from sentinel.strategy import scanner


def render() -> None:
    cfg = get_config()
    clock = get_clock()
    broker = get_broker()
    risk = get_risk_manager()

    # Header
    st.title("📈 Recommendations")
    st.caption(
        f"Triple-Check confluence: ≥{cfg.signals.min_signals_required} technical signals · macro regime · "
        f"sentiment ({cfg.sentiment.backend}). Click a card to add to your virtual portfolio."
    )

    # Top toolbar: scan + status
    tcol1, tcol2, tcol3 = st.columns([1.5, 4, 1.5])
    with tcol1:
        if st.button("🔄 Run scan now", type="primary", width="stretch"):
            with st.spinner("Scanning watchlist…"):
                recs = scanner.run_scan(cfg, cfg.storage.db_path, clock)
                from sentinel.analytics.performance import snapshot_today
                snapshot_today(cfg.storage.db_path, starting_equity=cfg.app.initial_virtual_equity)
            st.success(f"Scan done. {len(recs)} qualifying recs.")
            st.rerun()
    with tcol2:
        st.markdown(
            f"**Sentiment:** `{cfg.sentiment.backend}`  &nbsp; "
            f"**News sources:** {len(cfg.sentiment.news_sources.enabled)} active  &nbsp; "
            f"**Watchlist:** {len(cfg.watchlist.symbols)} symbols"
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    active = recommendations.list_active(cfg.storage.db_path, now_iso)

    if not active:
        _render_empty_state_diagnostic(cfg)
        return

    # ---------- card grid ----------
    st.markdown("### Active recommendations")
    # Two columns of cards
    cols = st.columns(2)
    for i, r in enumerate(active):
        with cols[i % 2]:
            _render_card(r, cfg, clock, broker, risk)


def _render_card(r: dict, cfg, clock, broker, risk) -> None:
    """One bordered card per recommendation."""
    try:
        signal_list = json.loads(r["signals_fired"])
    except (TypeError, json.JSONDecodeError):
        signal_list = []

    score = float(r["score"])
    score_color = "🟢" if score >= 0.7 else ("🟡" if score >= 0.5 else "🔵")

    with st.container(border=True):
        # Header row: symbol + score
        h1, h2 = st.columns([3, 2])
        with h1:
            st.markdown(f"## {r['symbol']}")
        with h2:
            st.metric("Score", f"{score_color} {score:.2f}")

        # Signal badges
        badges = " ".join(f"`{s}`" for s in signal_list) or "_(no signals)_"
        st.markdown(f"**Signals fired:** {badges}")

        # Pricing row
        entry = Decimal(r["entry_price_ref"])
        stop = Decimal(r["suggested_stop"])
        tp = Decimal(r["suggested_take_profit"]) if r["suggested_take_profit"] else None
        risk_per_share = entry - stop
        rr = float((tp - entry) / risk_per_share) if (tp and risk_per_share > 0) else None

        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Entry ref", money(entry))
        p2.metric("Stop", money(stop), delta=f"-{cfg.exit.hard_stop_pct:.1%}", delta_color="off")
        p3.metric("Target", money(tp) if tp else "trail")
        p4.metric("R:R", f"{rr:.1f}:1" if rr else "—")

        # Sentiment + timing
        sent = r["sentiment_score"]
        sent_str = f"{sent:+.2f}" if sent is not None else "—"
        st.caption(
            f"🧠 Sentiment: {sent_str}  ·  "
            f"⏱️ Created: {fmt_ts(r['created_at'])}  ·  "
            f"⌛ Expires: {fmt_ts(r['expires_at'])}"
        )

        # Forecast (from backtest history)
        stats = backtest_trades.stats_for_signal_mix(cfg.storage.db_path, signal_list) if signal_list else None
        if stats:
            verdict_emoji = (
                "✅" if stats["expectancy_pct"] > 0.001
                else "❌" if stats["expectancy_pct"] < -0.001
                else "⚪"
            )
            st.markdown(
                f"**Forecast** (from {stats['n']} prior backtest trades): "
                f"{verdict_emoji} expectancy {stats['expectancy_pct']:+.2%} per trade · "
                f"win rate {stats['win_rate']:.0%} · "
                f"avg win {stats['avg_win_pct']:+.2%} / avg loss {stats['avg_loss_pct']:+.2%}"
            )
        else:
            st.caption(
                "💡 No backtest history for this exact signal mix yet — "
                "run `sentinel backtest` to populate forecasts."
            )

        # Risk preview
        eq = compute_equity_state(cfg.storage.db_path, cfg.app.initial_virtual_equity, clock)
        intent = OrderIntent(
            symbol=r["symbol"], side=Side.LONG,
            signals_fired=tuple(), sentiment_score=sent or 0.0,
            entry_price=entry, stop_price=stop,
            take_profit=tp, bar_ts=clock.now(),
            source_recommendation_id=r["id"],
        )
        decision = risk.evaluate(intent, eq)

        if decision.approved:
            risk_dollars = decision.quantity * (entry - decision.stop_price)
            cap = decision.quantity * entry
            st.success(
                f"✅ RiskManager: **{decision.quantity} shares**  "
                f"(${risk_dollars:,.0f} at risk · ${cap:,.0f} notional)"
            )
            if st.button(
                f"➕ Add {decision.quantity} {r['symbol']} to virtual portfolio",
                key=f"add_{r['id']}", type="primary", width="stretch",
            ):
                fill = broker.submit_buy(
                    symbol=intent.symbol, qty=decision.quantity,
                    entry_price=intent.entry_price, stop_price=intent.stop_price,
                    take_profit=intent.take_profit, decision=decision,
                    source_recommendation_id=r["id"],
                )
                st.success(f"Filled: {fill.qty} {fill.symbol} @ {money(fill.price)} on {fmt_ts(fill.ts)}")
                st.rerun()
        else:
            gate = decision.gate.value if decision.gate else "unknown"
            st.error(f"❌ RiskManager rejected: **{gate}** — {'; '.join(decision.reasons)}")

        # Audit drawer
        with st.expander("Full rationale + audit JSON"):
            st.text(_extract_rationale(r))
            st.code(r["full_payload"], language="json")


def _extract_rationale(r: dict) -> str:
    try:
        payload = json.loads(r["full_payload"])
        return payload.get("rationale", "(no rationale)")
    except (TypeError, json.JSONDecodeError):
        return "(rationale unavailable)"


def _render_empty_state_diagnostic(cfg) -> None:
    """When no recs exist, show WHY by digesting the most recent scan's
    per-symbol signals + risk decisions from the decisions_log."""
    from collections import defaultdict
    from sentinel.storage.repos import decisions, watchlist

    st.markdown("### No active recommendations right now")

    # 1. Pull recent decisions
    sig_rows = decisions.list_recent(cfg.storage.db_path, kind="signal", limit=200)
    risk_rows = decisions.list_recent(cfg.storage.db_path, kind="risk", limit=100)
    regime_rows = decisions.list_recent(cfg.storage.db_path, kind="regime", limit=1)

    if not sig_rows:
        with st.container(border=True):
            st.warning(
                "**No scans have run yet.** Click **Run scan now** above to start.\n\n"
                "If you want it to run automatically, open a terminal and run "
                "`sentinel run --interval 5m` — it'll scan every 5 minutes during market hours."
            )
        return

    # 2. Macro regime check
    if regime_rows:
        try:
            regime = json.loads(regime_rows[0]["payload"])
            if regime.get("hostile"):
                with st.container(border=True):
                    st.error(
                        "🛑 **Macro regime is HOSTILE** — no new long entries allowed.\n\n"
                        "Reasons:\n" + "\n".join(f"- {r}" for r in regime.get("block_reasons", []))
                        + "\n\nThis blocks the entire scan. Recs resume when conditions clear."
                    )
                return
        except (TypeError, json.JSONDecodeError):
            pass

    # 3. Aggregate per-symbol signal results from the most recent scan
    syms = watchlist.list_all(cfg.storage.db_path)
    most_recent_ts = sig_rows[0]["ts"]   # ISO; rows are DESC ordered
    cutoff = most_recent_ts[:13]         # match by hour to capture one scan
    fired_per_symbol: dict[str, list[str]] = defaultdict(list)
    rejected_per_symbol: dict[str, str] = {}
    for r in sig_rows:
        if not r["ts"].startswith(cutoff):
            break
        sym = r["symbol"]
        if not sym:
            continue
        try:
            payload = json.loads(r["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        if payload.get("liquidity_block"):
            rejected_per_symbol[sym] = f"liquidity: {payload['liquidity_block']}"
        elif payload.get("fired"):
            fired_per_symbol[sym].append(payload.get("name", "?"))

    # 4. Build per-symbol summary
    summary_rows = []
    for s in syms:
        if s in rejected_per_symbol:
            summary_rows.append({"Symbol": s, "Status": "🚫 liquidity", "Detail": rejected_per_symbol[s]})
        else:
            fired = fired_per_symbol.get(s, [])
            need = cfg.signals.min_signals_required
            if len(fired) >= need:
                summary_rows.append({"Symbol": s, "Status": "🟡 not picked", "Detail": f"signals fired: {','.join(fired)} (something else blocked)"})
            elif len(fired) > 0:
                summary_rows.append({"Symbol": s, "Status": f"🟠 only {len(fired)}/{need}", "Detail": f"fired: {','.join(fired)}"})
            else:
                summary_rows.append({"Symbol": s, "Status": "⚪ no signals", "Detail": "—"})

    with st.container(border=True):
        st.markdown(f"**Last scan: {most_recent_ts[:19].replace('T', ' ')} UTC**")
        st.markdown(
            f"Confluence requires **≥{cfg.signals.min_signals_required} of "
            f"{len(cfg.signals.enabled)}** technical signals to fire on the same bar with regime OK."
        )

    if summary_rows:
        st.markdown("#### Per-symbol breakdown from the last scan")
        import pandas as pd
        df = pd.DataFrame(summary_rows).sort_values("Status")
        st.dataframe(df, hide_index=True, width="stretch")

    # 5. Concrete suggestions
    almost = [r for r in summary_rows if "🟠" in r["Status"]]
    nothing = [r for r in summary_rows if "⚪" in r["Status"]]

    with st.container(border=True):
        st.markdown("#### How to get more recommendations")
        st.markdown(
            f"""
1. **Add more volatile names.** Mega-caps rarely produce 2-of-3 confluence. Try:
   ```
   sentinel watch add SMCI COIN MARA PLTR AVGO MU SOFI HOOD
   ```
2. **Run during US market hours (9:30–16:00 ET).** Intraday signals don't fire meaningfully after-hours. Right now the system is using the most recent bar available.
3. **Loosen the threshold to see flow** (then turn it back up). Edit `config/default.yaml`:
   ```yaml
   signals:
     min_signals_required: 1   # was 2
   ```
   {"⚠️ This drops the strategy quality bar — only do it for testing." if cfg.signals.min_signals_required >= 2 else ""}
4. **Almost-fired this scan ({len(almost)}):** {', '.join(r['Symbol'] for r in almost) or '(none)'}
5. **Zero signals this scan ({len(nothing)}):** {', '.join(r['Symbol'] for r in nothing) or '(none)'}

If you want even more verbose data, the **Audit log** page shows every decision the system has made.
            """.strip()
        )
