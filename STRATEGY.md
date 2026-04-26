# Sentinel — Strategy Guide

What Sentinel actually does, what each signal means, and what to honestly expect.

The same content is rendered live in the **📖 Strategy Guide** page of the
dashboard, which always reflects your current `config/default.yaml` values.
This file is the version-controlled reference.

---

## The "Triple-Check" model

Sentinel produces a **BUY recommendation** for a symbol only when **all three** of the following are true at the same moment:

1. **Macro regime is clean** — no broad-market panic, no scheduled catalyst risk
2. **At least N technical signals fire** on the same bar (default: 2 of 3)
3. **Sentiment qualifies** — when FinBERT or news-driven sentiment is enabled, the symbol must have positive weighted sentiment from ≥ 3 trusted sources

This is **deliberately strict.** A typical day produces 0–5 recommendations on a watchlist of 10–20 names. That's by design — confluence is rare, and rare events are higher-quality.

**What we're NOT doing**: predicting price direction. We're identifying setups where multiple independent indicators agree that a tradable move is in progress, and then risk-managing aggressively so losses are bounded.

---

## The three technical signals

### Gap-and-Go
- **Looks for**: overnight gap ≥ 2% on relative volume ≥ 2.0× the 20-day ADV
- **Why it works**: gaps on volume reflect real catalysts (earnings, news) that institutions are positioning into; volume filters out illiquid/low-conviction gaps that fade
- **What gets caught**: post-earnings continuation, news-driven gaps, sector-rotation winners
- **Failure mode**: ~60% of gaps fade by mid-day. Mitigated by the optional `gap_followthrough` filter

### Mean Reversion (long)
- **Looks for**: price > 2.5σ below the 20-period VWAP AND RSI(14) < 30
- **Why it works**: statistically extended-down + oversold momentum has higher-than-random probability of bouncing
- **What gets caught**: macro-fear selloffs in quality names, capitulation candles, sector-rotation losers due for relief
- **Failure mode**: falling-knife problem. RSI alone isn't enough — true downtrends stay oversold for days. The macro regime gate helps but this signal still produces the most -1% stop-outs

### VWAP Reclaim
- **Looks for**: price crosses up through session VWAP on volume ≥ 1.5× the 20-bar average
- **Why it works**: session VWAP is the average institutional fill price for the day; reclaiming it on volume signals the end of an intraday selloff
- **What gets caught**: morning dip-buying, mid-day reclaims after panic spikes, late-day rallies into close
- **Failure mode**: bull traps in choppy regimes. Volume confirmation helps but ~30% of reclaims fail

---

## Liquidity prefilter

Applied before any signal evaluation. A symbol must pass **all three**:

- ADV ≥ 1,000,000 shares (so we can trade without moving price)
- Spread ≤ 0.10% (so transaction costs are bounded)
- Price ≥ $5 (no penny stocks; signals are unreliable below)

This prevents Sentinel from recommending illiquid microcaps that look great on backtests but can't actually be traded.

---

## Macro regime gate

Even if all three signals fire, we **block new entries** when:

- VIX > 28 (fear elevated; correlation across stocks goes to 1, signals stop working)
- VIX up > 15% on day (regime shift in progress)
- SPY/QQQ down > 1.5% intraday (broad selloff; macro overwhelms individual signals)
- Earnings within 48h for the specific symbol (binary risk; direction unknowable)
- FOMC/CPI/NFP day before release + 30min cooldown (scheduled volatility)

Most blow-ups happen because a strategy was great in a normal regime and got run over in an abnormal one. The regime gate is the single biggest piece of downside protection.

---

## Weighted sentiment

When enabled, each news article about a symbol gets a -1 to +1 score from a financial NLP model (FinBERT). We aggregate per symbol with two weights:

### Tier weights
| Tier | Weight | Sources |
|---|---:|---|
| 1 | 1.0 | Reuters, Bloomberg, WSJ, FT, SEC EDGAR, company PR |
| 2 | 0.5 | Benzinga, Seeking Alpha, CNBC, MarketWatch, Yahoo Finance, Barron's |
| Untiered | 0 | ChartMill, InvestorPlace, Motley Fool — cached but contribute 0 |

### Recency decay (piecewise linear)
- < 1h old: weight 1.0
- at 4h: weight 0.5
- at 24h: weight 0.0

### Qualification gate (for sentiment to qualify a rec)
- Weighted score > 0.7
- ≥ 3 independent canonical sources (Reuters via Yahoo = 1 source, not 2)
- Newest article < 4h old

### Honest caveats
- Default `stub` backend is always neutral (0). Triple-Check operates as "Double-Check" until you turn on FinBERT or get richer news flow.
- **NewsAPI's free tier delays articles by 24h**, making them effectively useless for the recency gate. **Finnhub** (real-time) is the source that moves the needle.
- Reddit / StockTwits / X are intentionally excluded — trusted financial sources only.

---

## Risk management

### Position sizing
Every position sized so a hard-stop hit loses **at most 0.5% of equity**:
```
qty = floor(equity × risk_per_trade_pct / |entry_price - stop_price|)
```
Then clamped by:
- Per-position cap: ≤ 25% of equity
- Total deployed cap: ≤ 40% of equity (60% cash floor)
- Concurrent positions: ≤ 5 open

Worst-case (5 trades all hit stop on the same day): **2.5% of equity total loss.** The system is designed to survive bad days, not maximize good ones.

### Layered exit logic (in priority order)

| Priority | Trigger | Action | Result |
|---|---|---|---|
| 1 | Mark ≥ entry + 5% | Take profit | Lock in gain |
| 2 | Peak hit ≥ +1.5%, mark drops 0.75% below peak | Trailing stop | Capture upside while it lasts |
| 3 | Peak hit ≥ +1%, mark falls back to entry | Break-even stop | Worst case = $0 |
| 4 | Mark ≤ entry - 1% | Hard stop | Initial backstop, only until #3 arms |

**The break-even stop is the most important rule for risk control**: once a trade has been at +1% in your favor, it can never close at a loss. The floor moves up to entry the moment a +1% peak is seen. This is the closest thing to "no losses" that's mathematically achievable in trading.

### Halts and circuit breakers

- Daily loss > 2% → halt new entries (existing positions continue)
- Weekly loss > 5% over rolling 5-session window → halt all trading
- 3 consecutive losing trades → halt new entries for the day
- > 20 orders in 5 minutes → halt + alert (runaway protection)
- 30-day correlation > 0.80 with an existing position → reject

These prevent a bad day from becoming a catastrophic week.

---

## What to honestly expect

### Realistic numbers (2-year backtest, daily bars)

| Metric | Baseline | Combined + layered exits | SPY buy-and-hold |
|---|---:|---:|---:|
| Total return (2y) | +7.83% | +6.72% | +25.49% |
| Sharpe ratio | 0.36 | **0.64** | 0.77 |
| Max drawdown | -8.81% | **-3.43%** | -19.00% |
| Win rate | 34% | **54%** | — |
| Trades / year | ~35 | ~28 | — |

### What this means honestly

- **We do NOT beat SPY on absolute return.** A buy-and-hold of SPY over the same 2 years made ~3× more money in absolute terms.
- **We DO beat SPY on risk-adjusted basis** (Sharpe + 5× lower max drawdown).
- **Appropriate for**: capital preservation, sleeping at night, recovering quickly from bad weeks.
- **Not appropriate for**: maximizing absolute returns in a roaring bull market.

### When Sentinel will produce 0 recommendations
- Calm market days with low volume
- After-hours scans (intraday signals don't fire on stale bars)
- Hostile macro regime (VIX > 28)
- Within 48h of major earnings on every watchlist symbol
- Watchlist too narrow (mega-caps rarely produce 2-of-3 confluence)

### When Sentinel will produce many recommendations
- Volatile sector-rotation days
- Post-earnings sessions (companies whose earnings are 1+ days behind us)
- Watchlist includes smaller-cap volatile names (SMCI, COIN, MARA, biotech)
- Market hours, especially the first 90 minutes (9:30-11:00 ET)

### The mental model

Don't think of Sentinel as a "stock picker" — think of it as a **disciplined risk-management framework that surfaces high-confluence setups**. The recommendations are the *outputs*; the *value* is the gates that prevent over-trading or over-sizing into bad setups.

---

## Glossary

- **VWAP** — Volume-Weighted Average Price; the average price weighted by trade volume. Institutional reference for "where to be buying/selling."
- **ATR** — Average True Range; volatility measure. Higher = more volatile.
- **RSI** — Relative Strength Index; 0-100 momentum oscillator. < 30 oversold, > 70 overbought.
- **R-multiple** — Profit/loss as a multiple of initial risk. A trade risking 1% gaining 3% = +3R.
- **Sharpe ratio** — (return - risk-free rate) / std dev. Higher = better return per unit of volatility.
- **Sortino ratio** — Like Sharpe but only penalizes downside volatility. Often more meaningful for asymmetric strategies.
- **Max drawdown** — Largest peak-to-trough decline in equity curve. Less negative = better capital preservation.
- **Confluence** — Multiple independent indicators aligning at the same moment. The whole point of Sentinel.
- **Catalyst** — Scheduled event known to move a stock (earnings, FOMC, CPI, FDA, IPO).
- **Blackout** — Window before/after a catalyst when we refuse new positions in the affected symbol.
- **Conviction (AI Analyst)** — `high` = ≥ 3 independent factors aligned; `medium` = 2; `low` = single factor.
