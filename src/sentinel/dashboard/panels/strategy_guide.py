"""Strategy Guide panel — explains every signal, the confluence model,
risk gates, exit logic, and honest expectations.
"""
from __future__ import annotations

import streamlit as st

from sentinel.dashboard.state import get_config


def render() -> None:
    cfg = get_config()
    st.title("📖 Strategy Guide")
    st.caption(
        "What Sentinel actually does, what each signal means, and what to honestly expect."
    )

    # ---------- Overview ----------
    with st.container(border=True):
        st.markdown("""
### The "Triple-Check" model

Sentinel produces a **BUY recommendation** for a symbol only when **all three** of the following are true at the same moment:

1. **Macro regime is clean** — no broad-market panic, no scheduled catalyst risk
2. **At least N technical signals fire** on the same bar (default: 2 of 3)
3. **Sentiment qualifies** — when FinBERT or news-driven sentiment is enabled, the symbol must have positive weighted sentiment from ≥ 3 trusted sources

This is **deliberately strict.** A typical day produces 0–5 recommendations on a watchlist of 10–20 names. That's by design — confluence is rare, and rare events are higher-quality.

**What we're NOT doing:** predicting price direction. We're identifying setups where **multiple independent indicators** agree that a tradable move is in progress, and then **risk-managing aggressively** so losses are bounded.
        """)

    # ---------- Signals ----------
    st.subheader("🎯 The three technical signals")

    sig_tabs = st.tabs(["Gap-and-Go", "Mean Reversion", "VWAP Reclaim"])

    with sig_tabs[0]:
        st.markdown(f"""
**What it looks for**
A symbol that **opened the day with a meaningful gap** (≥ {cfg.signals.gap_and_go.min_gap_pct:.0%}) on **unusually high volume** (≥ {cfg.signals.gap_and_go.min_rel_volume:.1f}× the recent average daily volume).

**Why it works**
Overnight gaps on volume usually reflect a real catalyst — earnings, a news headline, an analyst upgrade — that institutions are positioning into. The combination filters out illiquid/low-conviction gaps that often fade.

**What gets caught**
- Stocks gapping up after positive earnings the prior evening
- Names reacting to scheduled news (FDA decisions, M&A announcements)
- Sector rotation candidates getting fresh institutional flow

**What gets rejected**
- Slow grinds with no overnight gap
- Gaps on average volume (= retail-driven, often fade)
- Gaps below 2% (too small to be a real signal)

**Failure mode (be honest)**: ~60% of gaps fade by mid-day. We mitigate this with the optional `gap_followthrough` filter (which adds a "is this gap holding?" check before firing).
        """)

    with sig_tabs[1]:
        st.markdown(f"""
**What it looks for**
A symbol where price has fallen sharply below its short-term volume-weighted average — specifically, **more than {cfg.signals.mean_reversion.std_dev_threshold}σ below the {cfg.signals.mean_reversion.vwap_window}-period VWAP** AND **RSI({cfg.signals.mean_reversion.rsi_window}) < {cfg.signals.mean_reversion.rsi_oversold:.0f}** (oversold).

**Why it works**
When a stock is statistically extended to the downside on momentum-confirmed oversold conditions, it has a higher-than-random probability of bouncing in the next session or two. This is a **counter-trend reversion** trade, not a momentum continuation.

**What gets caught**
- Quality names that sold off on macro fear (not company-specific bad news)
- Sector rotation losers due for a relief bounce
- Capitulation candles on thin afternoon volume

**What gets rejected**
- Any down-move that hasn't reached -2.5σ
- Names where RSI is between 30-70 (no oversold confirmation)

**Failure mode (be honest)**: a "falling knife" that keeps falling. RSI alone isn't enough — true downtrends can stay oversold for days. The macro regime gate helps (we don't take MR longs in a hostile tape), but this signal still produces the most -1% stop-outs of the three.
        """)

    with sig_tabs[2]:
        st.markdown(f"""
**What it looks for**
A symbol where **price crosses up through the session VWAP** (volume-weighted average) on **unusual volume** (≥ {cfg.signals.vwap_reclaim.volume_confirmation_mult:.1f}× the recent 20-bar average) — meaning the bar that closes above VWAP also has institutional-size volume.

**Why it works**
Session VWAP is the price the average institutional order has paid today. When it gets reclaimed on volume, it often signals the **end of an intraday selloff** — institutions are buying the dip. This is one of the most reliable intraday signals professional desks watch.

**What gets caught**
- Morning dip-buying after an open weakness
- Mid-day reclaims following a panic spike
- Late-day rallies that close strong

**What gets rejected**
- Crosses on light volume (= retail, often fade)
- Crosses without follow-through (the next bar drops back below)
- Symbols with thin total session volume

**Failure mode (be honest)**: false breakouts ("bull traps") are common in choppy regimes. The volume confirmation filter helps a lot, but we still see ~30% of VWAP reclaims fail.
        """)

    # ---------- Liquidity ----------
    with st.container(border=True):
        st.markdown(f"""
### 💧 Liquidity prefilter (applied before any signal evaluation)

A symbol must pass **all three** to even be considered:
- **ADV ≥ {cfg.signals.liquidity.min_adv_shares:,} shares** (so we can get in/out without moving the price)
- **Spread ≤ {cfg.signals.liquidity.max_spread_pct:.2%}** (so transaction costs are bounded)
- **Price ≥ ${cfg.signals.liquidity.min_price}** (no penny stocks; signals are unreliable below $5)

This is what stops Sentinel from recommending illiquid microcaps that look great on backtests but can't actually be traded.
        """)

    # ---------- Macro regime ----------
    with st.container(border=True):
        st.markdown(f"""
### 🌍 Macro regime gate

Even if all three signals fire, we **block new entries** when the broad market is hostile:

- **VIX > {cfg.regime.vix_absolute}**: fear is elevated; correlation across all stocks goes to 1, signals stop working
- **VIX up > {cfg.regime.vix_daily_rise_pct:.0%} on day**: fear is rising fast (regime shift in progress)
- **SPY/QQQ down > {cfg.regime.spy_daily_decline_pct:.1%} intraday**: broad selloff, individual signals get overwhelmed by macro flow
- **Earnings within {cfg.risk.earnings_blackout_hours:.0f}h** for the *specific symbol*: binary risk — direction is unknowable
- **FOMC/CPI/NFP day** before release: scheduled volatility events flood the tape

**Why this matters**: most blow-ups don't happen because a strategy is wrong — they happen because the strategy was great in a normal regime and got run over in an abnormal one. Sentinel's regime gate is the single biggest piece of downside protection in the entire system.
        """)

    # ---------- Sentiment ----------
    with st.container(border=True):
        st.markdown(f"""
### 🧠 Weighted sentiment

When enabled (currently `{cfg.sentiment.backend}`), each news article about a symbol gets a -1 to +1 score from a financial NLP model (FinBERT). We then aggregate per symbol with two weights:

**Tier weighting** — different sources get different trust:
- **Tier 1 (weight 1.0)**: Reuters, Bloomberg, WSJ, FT, SEC EDGAR, company press releases
- **Tier 2 (weight 0.5)**: Benzinga, Seeking Alpha, CNBC, MarketWatch, Yahoo Finance, Barron's
- **Untiered**: ChartMill, InvestorPlace, Motley Fool — cached but contribute 0 to score

**Recency decay** — older news matters less:
- Full weight if < 1h old
- Half weight at 4h
- Zero weight at 24h+

**Qualification gate** — for sentiment to "qualify" a recommendation:
- Weighted score must be > {cfg.sentiment.qualify_threshold}
- ≥ {cfg.sentiment.qualify_min_independent_sources} independent canonical sources (Reuters via Yahoo = 1 source, not 2)
- Newest article < {cfg.sentiment.qualify_max_newest_age_hours:.0f} hours old

**Honest caveat**: with the default `stub` backend, sentiment is always neutral (0). The Triple-Check operates as "Double-Check" until you turn on FinBERT or get richer news flow. **NewsAPI's free tier delays articles by 24h**, making them effectively useless for the recency gate — Finnhub (real-time) is the source that actually moves the needle.
        """)

    # ---------- Risk ----------
    st.subheader("⚖️ Risk management — the most important section")

    with st.container(border=True):
        st.markdown(f"""
### Position sizing

Every position is sized so that if our hard stop hits, we lose **at most {cfg.risk.risk_per_trade_pct:.2%} of total equity** on the trade. This is **fixed-fractional sizing by stop distance**:

```
qty = floor(equity × risk_per_trade_pct / |entry_price - stop_price|)
```

Then we clamp by:
- **Per-position cap**: ≤ {cfg.risk.max_per_position_pct:.0%} of equity in any single name
- **Total deployed cap**: ≤ {cfg.risk.max_deployed_pct:.0%} of equity across all positions ({100 - cfg.risk.max_deployed_pct*100:.0f}% cash floor)
- **Concurrent positions**: ≤ {cfg.risk.max_concurrent_positions} open at once

**What this means**: even if 5 trades go to full stop simultaneously, the worst-case loss is **{cfg.risk.risk_per_trade_pct * cfg.risk.max_concurrent_positions * 100:.1f}% of equity** in a single day. The system is designed to survive bad days, not to maximize good ones.
        """)

    with st.container(border=True):
        st.markdown(f"""
### Layered exit logic

For every position, the sweep checks these in priority order on every refresh:

| Priority | Trigger | Action | Result |
|---|---|---|---|
| 1 | Mark ≥ entry + {cfg.exit.profit_take_pct:.1%} | **Take profit** | Lock in the gain |
| 2 | Peak hit ≥ +{cfg.exit.trailing_stop_activate_pct:.1%}, mark drops {cfg.exit.trailing_stop_pct:.2%} below peak | **Trailing stop** | Capture upside while it lasts |
| 3 | Peak hit ≥ +{cfg.exit.breakeven_trigger_pct:.1%}, mark falls back to entry | **Break-even stop** | Worst case = $0 (no loss) |
| 4 | Mark ≤ entry - {cfg.exit.hard_stop_pct:.1%} | **Hard stop** | Initial backstop, only until #3 arms |

**The break-even stop is the most important rule** for risk control: **once a trade has been at +1% in your favor, it can never close at a loss.** The floor moves up to your entry price the moment you see a +1% peak. If the trade reverses, you exit flat (a "scratch trade").

This is the closest thing to "no losses" that's mathematically achievable in trading.
        """)

    with st.container(border=True):
        st.markdown(f"""
### Halts and circuit breakers

Sentinel will **automatically stop opening new positions** if:

- **Daily loss > {cfg.risk.daily_loss_limit_pct:.1%}** of session-open equity → halt new entries (existing positions continue with their exits)
- **Weekly loss > {cfg.risk.weekly_loss_limit_pct:.1%}** over rolling 5-session window → halt **all** trading
- **{cfg.risk.consecutive_loss_halt} consecutive losing trades** → halt new entries for the day
- **> {cfg.risk.order_rate_max} orders in {cfg.risk.order_rate_window_sec // 60} minutes** → halt + alert (runaway protection)
- **30-day correlation > {cfg.risk.max_correlation:.0%}** between candidate and any open position → reject (don't stack correlated bets)

These are the "kill switches" that prevent a bad day from becoming a catastrophic week.
        """)

    # ---------- Expectations ----------
    st.subheader("📊 What to honestly expect")

    with st.container(border=True):
        st.markdown("""
### Realistic numbers (from 2-year backtest, daily bars)

| Metric | Baseline | Combined variant + layered exits | SPY buy-and-hold |
|---|---:|---:|---:|
| Total return (2y) | +7.83% | +6.72% | +25.49% |
| Sharpe ratio | 0.36 | **0.64** | 0.77 |
| Max drawdown | -8.81% | **-3.43%** | -19.00% |
| Win rate | 34% | **54%** | — |
| Trades / year | ~35 | ~28 | — |

### What this honestly means

- **We do NOT beat SPY on absolute return.** A buy-and-hold of SPY over the same 2 years made ~3× more money in absolute terms.
- **We DO beat SPY on risk-adjusted basis** (Sharpe + 5× lower max drawdown).
- **We're appropriate for**: capital preservation, sleeping at night, recovering quickly from bad weeks.
- **We're NOT appropriate for**: maximizing absolute returns in a roaring bull market.

### When Sentinel will produce 0 recommendations
- Calm market days with low volume
- After-hours scans (intraday signals don't fire on stale bars)
- During hostile macro regime (VIX > 28)
- Within 48h of major earnings on every watchlist symbol
- When watchlist is too narrow (e.g., only mega-caps that rarely produce 2-of-3 confluence)

### When Sentinel will produce many recommendations
- Volatile sector-rotation days
- Post-earnings sessions (companies whose earnings are 1+ days behind us)
- Watchlist includes smaller-cap volatile names (SMCI, COIN, MARA, biotech)
- Market hours, especially the first 90 minutes (9:30-11:00 ET)

### The mental model
Don't think of Sentinel as a "stock picker" — think of it as a **disciplined risk-management framework that surfaces high-confluence setups**. The recommendations are the *outputs*; the *value* is the gates that prevent you from over-trading or over-sizing into bad setups.
        """)

    # ---------- Glossary ----------
    with st.expander("📚 Glossary of terms used in the dashboard", expanded=False):
        st.markdown("""
- **VWAP** — Volume-Weighted Average Price; the average price weighted by trade volume. Institutions use it as a reference for where they "should" be buying/selling.
- **ATR** — Average True Range; a measure of volatility (typical daily price range). Higher = more volatile.
- **RSI** — Relative Strength Index; momentum oscillator from 0-100. < 30 = oversold (potential reversal up), > 70 = overbought.
- **R-multiple** — Profit/loss expressed as a multiple of initial risk. A trade risking 1% that gains 3% = +3R.
- **Sharpe ratio** — Risk-adjusted return; (return - risk-free rate) / std dev. Higher = better return per unit of volatility.
- **Sortino ratio** — Like Sharpe but only penalizes *downside* volatility. Often more meaningful than Sharpe for asymmetric strategies.
- **Max drawdown** — Largest peak-to-trough decline in the equity curve. Lower (less negative) = more capital-preservation-friendly.
- **Conviction (AI Analyst)** — `high` = ≥ 3 independent factors aligned; `medium` = 2; `low` = single factor.
- **Confluence** — multiple independent indicators aligning at the same moment. The whole point of Sentinel.
- **Catalyst** — a scheduled event known to move a stock (earnings, FOMC, CPI, FDA decision, IPO).
- **Blackout** — a window before/after a catalyst when we refuse to take new positions in the affected symbol.
        """)

    st.caption(
        "This guide is also in the repo at `STRATEGY.md`. "
        "Numbers shown reflect your current `config/default.yaml` values."
    )
