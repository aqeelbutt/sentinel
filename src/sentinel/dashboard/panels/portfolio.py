"""Open positions — current marks, unrealized P&L, time held, full lineage to source rec, manual close."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import streamlit as st

from sentinel.dashboard.format import fmt_ts, money, pct
from sentinel.dashboard.state import get_broker, get_config
from sentinel.data.market import yfinance_feed
from sentinel.storage.repos import positions, recommendations


def render() -> None:
    cfg = get_config()
    broker = get_broker()

    st.title("Open positions")
    st.caption(
        f"Profit-take auto-closes at +{cfg.exit.profit_take_pct:.1%}. "
        f"Hard stop at -{cfg.exit.hard_stop_pct:.1%}. Sweep runs every refresh + every `sentinel run` cycle."
    )

    open_pos = positions.list_open(cfg.storage.db_path)
    if not open_pos:
        st.info("No open positions. Add one from the Recommendations page or via `sentinel add`.")
        return

    total_unrealized = Decimal("0")
    rows: list[dict] = []
    for p in open_pos:
        quote = yfinance_feed.latest_quote(p["symbol"])
        mark = quote.last if quote else Decimal(p["avg_entry_price"])
        entry = Decimal(p["avg_entry_price"])
        qty = int(p["qty"])
        unreal = qty * (mark - entry)
        unreal_pct = float((mark - entry) / entry) if entry > 0 else 0.0
        total_unrealized += unreal
        opened = datetime.fromisoformat(p["opened_at"])
        held = (datetime.now(timezone.utc) - opened).total_seconds()
        if p["stop_price"]:
            stop = Decimal(p["stop_price"])
            r_unreal = unreal / (qty * (entry - stop)) if entry != stop else Decimal("0")
            r_str = f"{float(r_unreal):+.2f}R"
        else:
            r_str = "—"
        pt_target = entry * (Decimal("1") + Decimal(str(cfg.exit.profit_take_pct)))
        stop_target = entry * (Decimal("1") - Decimal(str(cfg.exit.hard_stop_pct)))
        dist_to_pt = float((pt_target - mark) / mark) if mark > 0 else 0.0
        dist_to_stop = float((mark - stop_target) / mark) if mark > 0 else 0.0

        rows.append({
            "id": p["id"], "symbol": p["symbol"], "qty": qty,
            "entry": entry, "mark": mark, "unreal": unreal, "unreal_pct": unreal_pct,
            "stop_price": p["stop_price"], "tp": p["take_profit"],
            "opened_at": p["opened_at"], "held": held, "r_str": r_str,
            "dist_to_pt": dist_to_pt, "dist_to_stop": dist_to_stop,
            "rec_id": p.get("source_recommendation_id"),
        })

    c1, c2, c3 = st.columns(3)
    c1.metric("Total unrealized P&L", money(total_unrealized))
    c2.metric("Open positions", f"{len(open_pos)}/{cfg.risk.max_concurrent_positions}")
    c3.metric("Notional at entry", money(sum(r["entry"] * r["qty"] for r in rows)))

    st.divider()
    st.subheader("Positions (click each for full audit)")

    for r in rows:
        emoji = "🟢" if r["unreal"] > 0 else ("🔴" if r["unreal"] < 0 else "⚪")
        with st.expander(
            f"{emoji}  **{r['symbol']}**  ·  {r['qty']}sh @ {money(r['entry'])}  ·  "
            f"mark {money(r['mark'])}  ·  unreal {money(r['unreal'])} ({pct(r['unreal_pct'])})  ·  "
            f"{r['r_str']}  ·  held {_dur(r['held'])}",
            expanded=False,
        ):
            colA, colB, colC = st.columns(3)
            with colA:
                st.markdown("**Position**")
                st.write(f"Qty: {r['qty']}")
                st.write(f"Avg entry: {money(r['entry'])}")
                st.write(f"Notional @ entry: {money(r['entry'] * r['qty'])}")
                st.write(f"Opened: {fmt_ts(r['opened_at'])}")
                st.write(f"Time held: {_dur(r['held'])}")
            with colB:
                st.markdown("**Mark / P&L**")
                st.write(f"Last quote: {money(r['mark'])}")
                st.write(f"Unrealized: {money(r['unreal'])} ({pct(r['unreal_pct'])})")
                st.write(f"R-multiple: {r['r_str']}")
                st.write(f"To profit-take (+{cfg.exit.profit_take_pct:.1%}): {pct(r['dist_to_pt'])}")
                st.write(f"To stop (-{cfg.exit.hard_stop_pct:.1%}): {pct(r['dist_to_stop'])}")
            with colC:
                st.markdown("**Risk**")
                st.write(f"Stop: {money(r['stop_price']) if r['stop_price'] else '—'}")
                st.write(f"TP: {money(r['tp']) if r['tp'] else 'trailing'}")
                if r["stop_price"]:
                    risked = r["qty"] * (r["entry"] - Decimal(r["stop_price"]))
                    st.write(f"$ at risk: {money(risked)}")

            if r["rec_id"]:
                rec = recommendations.get(cfg.storage.db_path, r["rec_id"])
                if rec:
                    st.markdown("---")
                    st.markdown("**Source recommendation**")
                    cD, cE = st.columns(2)
                    with cD:
                        st.write(f"Created at: {fmt_ts(rec['created_at'])}")
                        st.write(f"Score: {rec['score']:.2f}")
                        st.write(f"Signals: `{rec['signals_fired']}`")
                    with cE:
                        st.write(f"Suggested stop: {rec['suggested_stop']}")
                        st.write(f"Suggested TP: {rec['suggested_take_profit'] or 'trailing'}")
                        try:
                            rec_ts = datetime.fromisoformat(rec['created_at'])
                            opened_ts = datetime.fromisoformat(r['opened_at'])
                            st.write(f"Time rec→fill: {int((opened_ts - rec_ts).total_seconds())}s")
                        except ValueError:
                            pass
                    with st.expander("Full rec payload (audit JSON)", expanded=False):
                        try:
                            st.code(json.dumps(json.loads(rec["full_payload"]), indent=2), language="json")
                        except (TypeError, json.JSONDecodeError):
                            st.text(rec["full_payload"])

            st.markdown("---")
            if st.button(f"Close {r['symbol']} at market", key=f"close_{r['id']}", type="primary"):
                fill = broker.submit_sell(
                    symbol=r["symbol"], position_id=r["id"],
                    exit_price=r["mark"], close_reason="user",
                )
                st.success(f"Closed {fill.qty} {fill.symbol} @ {money(fill.price)} on {fmt_ts(fill.ts)}")
                st.rerun()


def _dur(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"
    days = int(seconds // 86400)
    return f"{days}d {int((seconds % 86400) // 3600)}h"
