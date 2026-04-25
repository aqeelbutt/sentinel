"""Closed trades — every trade with full lineage:
when the rec was made, when the order filled, when it was sold, why, P&L, R-multiple."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import streamlit as st

from sentinel.dashboard.format import fmt_ts, money, pct, trade_duration
from sentinel.dashboard.state import get_config
from sentinel.storage.repos import positions, recommendations


def render() -> None:
    cfg = get_config()
    st.title("Closed trades")
    st.caption("Full audit trail per trade — recommendation timestamp, fill, exit, reason, P&L, R-multiple.")

    closed = positions.list_closed(cfg.storage.db_path)
    if not closed:
        st.info("No closed trades yet.")
        return

    # ---------- summary metrics ----------
    realized_total = Decimal("0")
    wins = losses = 0
    r_multiples: list[float] = []
    for p in closed:
        pnl = Decimal(p["realized_pnl"]) if p["realized_pnl"] else Decimal("0")
        realized_total += pnl
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        if p["stop_price"]:
            entry = Decimal(p["avg_entry_price"])
            stop = Decimal(p["stop_price"])
            if entry != stop:
                r = pnl / (Decimal(p["qty"]) * (entry - stop))
                r_multiples.append(float(r))
    win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0.0
    avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Realized P&L", money(realized_total))
    c2.metric("Trades", len(closed))
    c3.metric("Wins", wins)
    c4.metric("Losses", losses)
    c5.metric("Win rate", f"{win_rate:.0%}")
    c6.metric("Avg R", f"{avg_r:+.2f}R")

    # ---------- export ----------
    st.download_button(
        "📥 Download all closed trades (CSV)",
        data=pd.DataFrame(closed).to_csv(index=False).encode("utf-8"),
        file_name=f"sentinel_trades_{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.csv",
        mime="text/csv",
    )

    st.divider()
    st.subheader("Trade ledger (click to expand each)")

    rec_cache: dict[str, dict] = {}
    for trade in closed:
        rec_id = trade.get("source_recommendation_id")
        if rec_id and rec_id not in rec_cache:
            rec_cache[rec_id] = recommendations.get(cfg.storage.db_path, rec_id) or {}
        rec = rec_cache.get(rec_id) if rec_id else None

        entry = Decimal(trade["avg_entry_price"])
        exit_p = Decimal(trade["exit_price"])
        pnl = Decimal(trade["realized_pnl"])
        pnl_pct_v = trade["realized_pnl_pct"] or 0.0
        if trade["stop_price"]:
            stop = Decimal(trade["stop_price"])
            r = pnl / (Decimal(trade["qty"]) * (entry - stop)) if entry != stop else Decimal("0")
            r_str = f"{float(r):+.2f}R"
        else:
            r_str = "—"

        # Time-to-fill: rec_created_at → opened_at
        fill_latency = "—"
        if rec and rec.get("created_at"):
            try:
                rec_ts = datetime.fromisoformat(rec["created_at"])
                opened_ts = datetime.fromisoformat(trade["opened_at"])
                seconds = (opened_ts - rec_ts).total_seconds()
                if seconds < 60:
                    fill_latency = f"{int(seconds)}s"
                elif seconds < 3600:
                    fill_latency = f"{int(seconds // 60)}m{int(seconds % 60):02d}s"
                else:
                    fill_latency = f"{int(seconds // 3600)}h{int((seconds % 3600) // 60):02d}m"
            except (ValueError, TypeError):
                pass

        emoji = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")
        with st.expander(
            f"{emoji}  **{trade['symbol']}**  ·  {trade['qty']}sh  ·  "
            f"{money(pnl)} ({pct(pnl_pct_v)})  ·  {r_str}  ·  "
            f"{trade['close_reason']}  ·  closed {fmt_ts(trade['closed_at'])}",
            expanded=False,
        ):
            colA, colB, colC = st.columns(3)
            with colA:
                st.markdown("**Entry**")
                st.write(f"Price: {money(entry)}")
                st.write(f"At: {fmt_ts(trade['opened_at'])}")
                st.write(f"Stop: {money(trade['stop_price']) if trade['stop_price'] else '—'}")
                st.write(f"TP: {money(trade['take_profit']) if trade['take_profit'] else 'trailing'}")
            with colB:
                st.markdown("**Exit**")
                st.write(f"Price: {money(exit_p)}")
                st.write(f"At: {fmt_ts(trade['closed_at'])}")
                st.write(f"Reason: `{trade['close_reason']}`")
                st.write(f"Held: {trade_duration(trade['opened_at'], trade['closed_at'])}")
            with colC:
                st.markdown("**P&L**")
                st.write(f"Realized: {money(pnl)}")
                st.write(f"%: {pct(pnl_pct_v)}")
                st.write(f"R-multiple: {r_str}")
                st.write(f"Fill latency: {fill_latency}")

            if rec:
                st.markdown("---")
                st.markdown("**Source recommendation**")
                colD, colE = st.columns(2)
                with colD:
                    st.write(f"Created at: {fmt_ts(rec['created_at'])}")
                    st.write(f"Score: {rec['score']:.2f}")
                    st.write(f"Signals: `{rec['signals_fired']}`")
                with colE:
                    st.write(f"Suggested stop: {rec['suggested_stop']}")
                    st.write(f"Suggested TP: {rec['suggested_take_profit'] or 'trailing'}")
                    st.write(f"Suggested qty: {rec['suggested_qty']}")
                with st.expander("Full rec payload (audit JSON)", expanded=False):
                    try:
                        st.code(json.dumps(json.loads(rec["full_payload"]), indent=2), language="json")
                    except (TypeError, json.JSONDecodeError):
                        st.text(rec["full_payload"])
            else:
                st.caption("(no source recommendation — likely a manual `sentinel add`)")

    # ---------- breakdown chart ----------
    st.divider()
    st.subheader("Closes by reason")
    df_reasons = pd.DataFrame([
        {"reason": p["close_reason"], "pnl": float(p["realized_pnl"] or 0)} for p in closed
    ])
    if not df_reasons.empty:
        agg = df_reasons.groupby("reason").agg(count=("pnl", "size"), total_pnl=("pnl", "sum")).reset_index()
        st.dataframe(agg, hide_index=True, width="stretch")
