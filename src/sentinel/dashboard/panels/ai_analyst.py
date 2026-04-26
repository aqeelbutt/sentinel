"""AI Analyst panel — Claude's recommendations + the exact data lineage that produced them.

Every recommendation card shows:
  * Thesis (the analyst's reasoning)
  * Catalysts cited
  * Conviction + time horizon
  * Risk factors
  * Inputs used (data lineage from the snapshot)
  * Cost in $
  * Add-to-portfolio button (still passes RiskManager, same as everything else)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import streamlit as st

from sentinel.core.types import OrderIntent, Side
from sentinel.dashboard.format import fmt_ts, money
from sentinel.dashboard.state import get_broker, get_clock, get_config, get_risk_manager
from sentinel.data.market import yfinance_feed
from sentinel.intelligence.claude_analyst import AIAnalystError, run_analyst
from sentinel.ops.keychain import get_secret
from sentinel.risk.manager import compute_equity_state
from sentinel.storage.repos import ai_recommendations as ai_repo


def render() -> None:
    cfg = get_config()
    clock = get_clock()
    broker = get_broker()
    risk = get_risk_manager()

    st.title("🧠 AI Analyst")
    st.caption(
        "Claude reads a structured market snapshot (movers + regime + catalysts + news + your portfolio) "
        "and produces evidence-grounded trade ideas. Every rec shows the exact data points it used."
    )

    has_key = bool(get_secret("anthropic"))
    c1, c2, c3 = st.columns([1.5, 1.5, 5])
    with c1:
        scan_clicked = st.button("🤖 Run AI scan", type="primary", width="stretch", disabled=not has_key)
    with c2:
        model = st.selectbox(
            "Model",
            ["claude-sonnet-4-6", "claude-opus-4-7", "claude-haiku-4-5"],
            index=0, label_visibility="collapsed",
        )
    with c3:
        if not has_key:
            st.error(
                "**Anthropic API key not configured.** Run in terminal:\n"
                "```\nsentinel keychain set anthropic <your-anthropic-api-key>\n```\n"
                "Get a key at https://console.anthropic.com"
            )
        else:
            st.caption(
                f"Key OK · model **{model}** · cost ~$0.005-0.08/scan depending on snapshot size · "
                f"idempotent within 5min (no spend on repeat clicks)"
            )

    if scan_clicked and has_key:
        with st.spinner("Claude analyzing the tape…"):
            try:
                fresh = run_analyst(cfg, cfg.storage.db_path, model=model)
                st.success(f"AI scan complete — {len(fresh)} recommendations")
            except AIAnalystError as e:
                st.error(f"AI analyst failed: {e}")

    # ---------- show recent recs ----------
    recent = ai_repo.list_recent(cfg.storage.db_path, limit=10)
    if not recent:
        st.info("No AI recommendations yet. Click **Run AI scan** above. (Requires an Anthropic API key.)")
        return

    # Cost tally for visibility
    total_cost = sum(r.get("cost_usd") or 0 for r in recent)
    st.caption(f"Last 10 recs · cumulative API spend: **${total_cost:.4f}**")

    cols = st.columns(2)
    for i, r in enumerate(recent):
        with cols[i % 2]:
            _render_card(r, cfg, clock, broker, risk)


def _render_card(r: dict, cfg, clock, broker, risk) -> None:
    conviction = r.get("conviction", "low")
    color = {"high": "🟢", "medium": "🟡", "low": "🔵"}.get(conviction, "⚪")
    with st.container(border=True):
        h1, h2 = st.columns([3, 2])
        with h1:
            st.markdown(f"## {color} {r['symbol']}")
            st.caption(
                f"**{conviction.upper()}** conviction · "
                f"horizon: **{r.get('time_horizon', '?')}** · "
                f"{fmt_ts(r['created_at'])}"
            )
        with h2:
            quote = yfinance_feed.latest_quote(r["symbol"])
            if quote:
                st.metric("Last", money(quote.last))

        st.markdown(f"**Thesis:** {r['thesis']}")

        if r.get("catalysts_cited"):
            badges = " ".join(f"`{c}`" for c in r["catalysts_cited"])
            st.markdown(f"**Catalysts cited:** {badges}")

        if r.get("risk_factors"):
            with st.expander("⚠️ Risk factors", expanded=False):
                for risk_item in r["risk_factors"]:
                    st.markdown(f"- {risk_item}")

        with st.expander("🔍 Data lineage (what the model actually used)", expanded=False):
            if r.get("inputs_used"):
                for inp in r["inputs_used"]:
                    st.markdown(f"- `{inp}`")
            else:
                st.caption("(no specific inputs cited)")
            st.caption(
                f"Model: `{r['model']}` · "
                f"snapshot hash: `{r['inputs_hash']}` · "
                f"cost: ${r.get('cost_usd') or 0:.4f}"
            )

        with st.expander("🤖 Raw model response", expanded=False):
            st.code(r.get("raw_response") or "(empty)", language="json")

        st.markdown("---")
        # Action: add to virtual portfolio (same risk gate as everything else)
        already_acted = r.get("acted_on")
        if already_acted:
            st.success(f"✅ Already added to portfolio (position id: `{r.get('related_position_id')}`)")
        else:
            quote = yfinance_feed.latest_quote(r["symbol"])
            if not quote:
                st.warning("Live quote unavailable — refresh and try again.")
                return
            entry = quote.last
            stop = entry * (Decimal("1") - Decimal(str(cfg.exit.hard_stop_pct)))
            tp = entry * (Decimal("1") + Decimal(str(cfg.exit.profit_take_pct)))
            intent = OrderIntent(
                symbol=r["symbol"], side=Side.LONG,
                signals_fired=tuple(), sentiment_score=0.0,
                entry_price=entry, stop_price=stop, take_profit=tp,
                bar_ts=clock.now(),
            )
            eq = compute_equity_state(cfg.storage.db_path, cfg.app.initial_virtual_equity, clock)
            decision = risk.evaluate(intent, eq)
            if decision.approved:
                if st.button(
                    f"➕ Add {decision.quantity} {r['symbol']} (${decision.quantity * entry:,.0f} notional)",
                    key=f"ai_add_{r['id']}", type="primary", width="stretch",
                ):
                    fill = broker.submit_buy(
                        symbol=r["symbol"], qty=decision.quantity,
                        entry_price=entry, stop_price=stop, take_profit=tp,
                        decision=decision, source_recommendation_id=None,
                    )
                    # Mark the AI rec as acted
                    pos_id = None
                    from sentinel.storage.repos import positions as _pos
                    p = _pos.get_open_by_symbol(cfg.storage.db_path, r["symbol"])
                    if p:
                        pos_id = p["id"]
                    ai_repo.mark_acted_on(cfg.storage.db_path, r["id"], pos_id or "")
                    st.success(f"Filled {fill.qty} {fill.symbol} @ {money(fill.price)}")
                    st.rerun()
            else:
                gate = decision.gate.value if decision.gate else "?"
                st.error(f"❌ RiskManager rejected: **{gate}** — {'; '.join(decision.reasons)}")
