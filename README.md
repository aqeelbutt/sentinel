# Sentinel

> Local-first equity recommendation engine + virtual portfolio tracker.
> Triple-Check confluence model (technical + sentiment + macro regime).
> No broker connection in Phase 1 — recommendations only, real money never at risk.

---

## What it does

Sentinel scans a watchlist of US equities and ETFs, applies a three-leg confluence model, and produces ranked **recommendations**. You decide which (if any) to add to a virtual portfolio. The system then tracks every virtual trade — buy time, sell time, P&L, close reason — and reports performance versus SPY buy-and-hold over rolling 1/2/3/4-week (and longer) windows.

A **profit-take** mechanism auto-closes any virtual position that hits a configurable gain (default **+5%**), and an **auto-stop** auto-closes positions hitting -1% (configurable). Both run on every dashboard refresh and after every scan.

When you've watched the system for several weeks and want to graduate it to a real broker, the architecture (a `BrokerPort` Protocol with `VirtualBroker` as the only Phase 1 implementation) is designed so an `AlpacaBroker` slots in without touching strategy or risk code. That switch requires explicit triple-confirmation and is **not enabled in this codebase**.

---

## How a recommendation is produced (Triple-Check)

A symbol becomes a BUY recommendation only when **all three** legs pass:

| Leg | Requirement |
| --- | --- |
| **Macro regime gate** | SPY above 200-period 5m VWAP · SPY/QQQ not down > 1.5% intraday · VIX < 28 and not up > 15% on the day · no FOMC/CPI/NFP within cooldown |
| **Technical signals** (≥ 2 firing) | **Gap-and-Go** (overnight gap > 2% on >2× ADV) · **Mean Reversion** (price > 2.5σ below 20-period VWAP and RSI(14) < 30) · **VWAP Reclaim** (close crosses session VWAP up with volume > 1.5× 20-bar avg) |
| **Sentiment** (Phase 2; permissive in Phase 1) | Weighted aggregate > 0.7 across ≥ 3 *independent* trusted sources, newest article < 4h old. Recency decay: 1.0 < 1h, 0.5 at 4h, 0.0 at 24h |

Liquidity prefilter: ADV > 1M shares, spread < 0.1%, price > $5 (no penny stocks).

### News sources (trusted financial only — no social media)

| Tier | Weight | Sources | Cost |
| --- | --- | --- | --- |
| 1 | 1.0 | Reuters, Bloomberg, WSJ, FT, SEC EDGAR, company PR | EDGAR free; others paid for full access |
| 2 | 0.5 | Benzinga, Seeking Alpha, CNBC, MarketWatch, Yahoo Finance, Barron's | Mostly free via RSS / yfinance |

**Reddit, StockTwits, X are intentionally excluded.** Out of the box, Sentinel uses Yahoo Finance news (free via `yfinance`) and Reuters / CNBC / MarketWatch RSS (free). SEC EDGAR is wired up but disabled until you set a real email in `sentiment.news_sources.edgar_user_agent` (SEC requires it).

---

## Setup (macOS)

### Prerequisites

- **Python 3.11 or newer**. Verify with `python3 --version`. If you have 3.10 or older, upgrade via Homebrew (`brew install python@3.12`).
- (Optional but recommended) **uv** — fast dependency manager. Install via `curl -LsSf https://astral.sh/uv/install.sh | sh`. Plain `pip` works too.
- An internet connection (yfinance and the RSS feeds need it).

### Install

```bash
cd /Users/aqeelbutt/Downloads/sentinel

# OPTION A — uv (recommended; fastest)
uv sync                     # creates .venv/ and installs everything
source .venv/bin/activate

# OPTION B — plain pip
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Verify

```bash
sentinel doctor
```

Expected:

```
                       Sentinel health check
┏━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Check    ┃ Status ┃ Detail                                     ┃
┡━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ python   │ OK     │ 3.12.5                                     │
│ imports  │ OK     │ all 12 core imports OK                     │
│ config   │ OK     │ loaded; mode=virtual, watchlist=10 symbols │
│ storage  │ OK     │ db at data/sentinel.db (schema v1)         │
│ yfinance │ OK     │ SPY last=708.44                            │
│ logs     │ OK     │ logs                                       │
│ nlp      │ OK     │ backend=stub (FinBERT not required)        │
└──────────┴────────┴────────────────────────────────────────────┘

All checks passed.
```

If any line is `FAIL`, the **Detail** column tells you exactly what to fix. The most common failures and their fixes are at the bottom of this README.

---

## Daily usage

```bash
# Launch the UI (opens in browser at http://127.0.0.1:8501)
sentinel dashboard

# Run a scan from the CLI without the UI
sentinel scan

# Supervisor loop — scan + sweep every N minutes during market hours (Ctrl-C to stop)
sentinel run --interval 5m

# See current virtual portfolio
sentinel list

# Manually buy a ticker at last quote (still passes RiskManager)
sentinel add AAPL --qty 50

# Close a position at market
sentinel close AAPL

# Force the profit-take / auto-stop sweep
sentinel sweep

# Performance vs SPY for a window (live virtual portfolio)
sentinel report --window 1w
sentinel report --window 1mo

# Manage watchlist
sentinel watch list
sentinel watch add NVDA AVGO MU
sentinel watch remove TSLA

# API keys (stored in macOS Keychain — never written to disk)
sentinel keychain set finnhub <key>
sentinel keychain set newsapi <key>
sentinel keychain list

# Backtests — replay history through the same strategy code
sentinel backtest --start 2024-04-01 --end 2026-04-01 --timeframe 1d
sentinel backtest --start 2024-04-01 --end 2026-04-01 --variant combined --timeframe 1d
```

### Strategy variants

The backtest accepts `--variant` to A/B test research-backed augmentations:

| Variant | What it changes |
|---|---|
| `baseline` | Current live strategy unchanged — reference point |
| `trend_filter` | Long entries gated by price > SMA(50) |
| `atr_stops` | Stops/targets at multiples of ATR(14), adaptive to volatility |
| `gap_followthrough` | Gap-and-Go requires the post-gap bar to confirm |
| `combined` | All three above |

Latest results (2-year daily, 17-symbol watchlist) — see [`CHANGELOG.md`](CHANGELOG.md) for the full table. **The `combined` variant ties SPY on Sharpe (0.69 vs 0.77) with -3.66% max DD vs SPY's -19%.** Live recommendations still use `baseline` until further validation.

### The dashboard at a glance

| Page | What it shows |
| --- | --- |
| **Recommendations** | Live ranked recommendations with full rationale. Pick one, RiskManager sizes it, click "Add to virtual portfolio". |
| **Open positions** | Every open position with entry price, current mark, unrealized P&L $/%, time held, stop, TP. Manual close button. |
| **Closed trades** | Every closed virtual trade with **bought-at**, **bought-time**, **sold-at**, **sold-time**, **duration**, **P&L $/%**, and **close reason** (`user`, `profit_take`, `hard_stop`, `trailing_stop`, `time`). |
| **Performance** | Equity curve indexed to 100, plotted alongside SPY buy-and-hold. Per-window metrics table: total return, Sharpe, Sortino, max drawdown, win rate, alpha vs SPY, **verdict** (BEATS / TIES / LOSES / INCONCLUSIVE). |
| **Watchlist** | Add / remove tickers from the scan universe. |
| **Audit log** | Every signal evaluation, sentiment score, regime check, risk decision, fill, and close — filterable by kind / symbol. |

The dashboard runs the profit-take + auto-stop sweep on every page load, so positions hitting +5% (or whatever you've set) auto-close even if you only refresh the UI.

---

## What "as real as possible" means here

| Component | Real or simulated? |
| --- | --- |
| Market data (bars / quotes) | **Real** — yfinance, free |
| Macro state (SPY, QQQ, VIX) | **Real** — yfinance |
| Macro calendar (FOMC / CPI / NFP) | **Real**, hardcoded for 2026 (replace `data/macro/calendar.py` to extend) |
| News headlines | **Real** for Yahoo Finance + Reuters/CNBC/MarketWatch RSS + SEC EDGAR (when configured) |
| Sentiment scoring | **Stub by default** (returns neutral, no qualifying sentiment). Optional **real FinBERT** via the `finbert` extra |
| Order fills | **Simulated** — fills at last quote ± 2bp slippage. No real money moves. |
| Stops / profit-takes | **Enforced locally** by `VirtualBroker.sweep_open_positions()`, since there is no broker holding native stops |

### Enabling FinBERT (real NLP)

```bash
uv sync --extra finbert         # ~500 MB download (torch + transformers + model weights)
# or: pip install -e '.[finbert]'

# then edit config/default.yaml:
sentiment:
  backend: finbert              # was: stub
```

After that, sentiment scoring runs real FinBERT inference on every news article. First scan after enabling will be slower (model load).

### Enabling SEC EDGAR (real filings)

EDGAR requires a real email in the User-Agent. Edit `config/default.yaml`:

```yaml
sentiment:
  news_sources:
    edgar_user_agent: "Sentinel your-name@your-domain.com"
```

Then EDGAR 8-K / 10-Q / 10-K filings appear as Tier-1 sentiment input.

---

## Configuration reference

All knobs are in `config/default.yaml` and validated by `sentinel/config/schema.py`. Key sections:

| Section | What it controls |
| --- | --- |
| `app.initial_virtual_equity` | Starting virtual cash (default $100,000) |
| `watchlist.symbols` | Symbols scanned each run (also editable from the UI / `sentinel watch`) |
| `data.bar_timeframe` | Intraday bar size for signals (default 5m) |
| `regime.*` | Macro gate thresholds (VIX, SPY/QQQ daily decline, VWAP window) |
| `signals.min_signals_required` | How many of {gap-and-go, mean-reversion, vwap-reclaim} must fire |
| `signals.liquidity.*` | ADV / spread / price floor for symbols |
| `sentiment.backend` | `stub` (default) or `finbert` |
| `sentiment.news_sources.enabled` | Which news fetchers to call |
| `risk.risk_per_trade_pct` | Per-trade risk budget (default 0.5% of equity) |
| `risk.max_concurrent_positions` | Position-count cap (default 5) |
| `risk.max_per_position_pct` / `risk.max_deployed_pct` | Per-position and total deployment caps |
| `risk.daily_loss_limit_pct` / `risk.weekly_loss_limit_pct` | Auto-halt thresholds |
| **`exit.profit_take_pct`** | **Auto-close profit threshold (default 5%)** |
| `exit.profit_take_enabled` / `exit.auto_stop_enabled` | Toggle the auto-exit sweep |
| `exit.hard_stop_pct` | Hard-stop floor enforced by sweep |

After editing, just rerun `sentinel scan` or refresh the dashboard — config is reloaded each invocation.

---

## How profit-take works

The dashboard runs `broker.sweep_open_positions()` on every page render (and `sentinel sweep` runs it on demand). For each open position:

1. Pull current quote via yfinance.
2. Compute `unrealized_pnl_pct = (mark - avg_entry_price) / avg_entry_price`.
3. If `unrealized_pnl_pct >= exit.profit_take_pct` → close with `close_reason="profit_take"`.
4. Else if `unrealized_pnl_pct <= -exit.hard_stop_pct` → close with `close_reason="hard_stop"`.

Each auto-close writes a row to `virtual_positions` (status=`closed`, with `close_reason`, `exit_price`, `closed_at`) and an entry to `decisions_log`. They show up in the **Closed trades** page immediately, with the reason visible.

To disable either: set `exit.profit_take_enabled: false` or `exit.auto_stop_enabled: false` in the YAML.

---

## Project layout

```
sentinel/
├── CLAUDE.md                  detailed context for Claude Code sessions
├── README.md                  this file
├── pyproject.toml             dependencies (uv/pip/hatch)
├── config/
│   └── default.yaml           runtime config (edit freely)
├── src/sentinel/
│   ├── config/schema.py       pydantic config models
│   ├── core/
│   │   ├── clock.py           NYSE-aware MarketClock + Clock protocol
│   │   ├── types.py           domain types (frozen dataclasses, Decimal money)
│   │   ├── identity.py        Phase 1 always-virtual mode token
│   │   └── config.py          YAML loader
│   ├── storage/
│   │   ├── schema.sql         SQLite schema (single migration)
│   │   ├── db.py              connection + init_db
│   │   └── repos/             one module per table
│   ├── data/
│   │   ├── market/            yfinance feed + bar cache
│   │   ├── macro/             SPY/QQQ/VIX regime + FOMC/CPI/NFP calendar
│   │   └── news/              real news fetchers (Yahoo, RSS, SEC EDGAR)
│   ├── sentiment/
│   │   ├── base.py            NLPBackend Protocol
│   │   ├── stub.py            neutral default
│   │   ├── finbert.py         opt-in real model
│   │   └── weighted.py        tiered + recency-decayed aggregator
│   ├── strategy/
│   │   ├── indicators.py      VWAP, RSI, ATR, ADV
│   │   ├── signals/           gap_and_go, mean_reversion, vwap_reclaim
│   │   ├── liquidity.py       ADV/spread/price floor
│   │   ├── confluence.py      assembles a Recommendation
│   │   └── scanner.py         end-to-end scan against the watchlist
│   ├── risk/
│   │   ├── manager.py         RiskManager (the gate)
│   │   ├── sizing.py          fixed-fractional + caps
│   │   └── correlation.py     30d correlation check
│   ├── execution/
│   │   ├── broker_port.py     BrokerPort Protocol
│   │   └── virtual_broker.py  VirtualBroker (fills + profit-take + auto-stop)
│   ├── analytics/
│   │   └── performance.py     Sharpe / Sortino / MDD / vs-SPY metrics
│   ├── ops/
│   │   ├── logging_setup.py   structlog → JSONL + console
│   │   └── health.py          `sentinel doctor` checks
│   ├── dashboard/
│   │   ├── app.py             Streamlit entry
│   │   └── panels/            per-page renderers
│   └── cli.py                 typer CLI
├── tests/unit/                pytest tests (clock, sizing, sentiment, broker, risk)
├── data/                      created at first run; SQLite DB lives here (gitignored)
└── logs/                      created at first run; JSONL logs (gitignored)
```

---

## How data flows

```
                   ┌──────────────────────────┐
                   │ config/default.yaml      │
                   └────────┬─────────────────┘
                            │
                            ▼
   ┌────────┐     ┌──────────────────┐      ┌─────────────────┐
   │ yfinance├───►│  bar_store       ├─────►│ MacroRegimeGate │
   └────────┘     │  (SQLite cache)  │      └────────┬────────┘
                  └──────────────────┘               │
   ┌──────────────┐                                  │
   │ Reuters RSS  │      ┌─────────────────┐         │
   │ Yahoo News   ├─────►│ news.aggregator ├─┐       │
   │ SEC EDGAR    │      └─────────────────┘ │       │
   └──────────────┘                          │       │
                                             ▼       ▼
                          ┌───────────────────────────────────────┐
                          │           StrategyScanner             │
                          │  signals → confluence → Recommendation │
                          └────────────────┬──────────────────────┘
                                           │
                                           ▼
                   ┌──────────────────────────────────┐
                   │  Streamlit dashboard / sentinel  │
                   │   "Add to virtual portfolio"     │
                   └────────────────┬─────────────────┘
                                    │
                                    ▼
                          ┌───────────────────┐
   user click  ─────────► │  RiskManager      │ ← decisions_log (audit)
                          │  .evaluate(intent)│
                          └────────┬──────────┘
                                   │ RiskDecision (approved=True, qty, COID)
                                   ▼
                          ┌────────────────────┐
                          │  VirtualBroker     │ ← virtual_orders (idempotency)
                          │  .submit_buy()     │ → virtual_positions (open)
                          └────────┬───────────┘
                                   │
                       (every refresh / scan)
                                   ▼
                          ┌────────────────────┐
                          │  sweep_open_       │ → virtual_positions (closed)
                          │  positions()       │   close_reason = profit_take|hard_stop
                          └────────────────────┘
```

---

## Tests

```bash
pytest                   # all tests, ~2s
pytest -m "not slow"     # skips FinBERT/network-heavy
```

31 tests cover `MarketClock` (DST + early-close + exit windows), `RiskManager` (every gate), `VirtualBroker` (fills, idempotency, profit-take, auto-stop sweep), sizing math, and sentiment recency decay.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `sentinel: command not found` after install | The venv isn't active — `source .venv/bin/activate` |
| `sentinel doctor` says **imports FAIL** | `pip install -e ".[dev]"` (or `uv sync`) again from the repo root |
| `sentinel doctor` says **yfinance FAIL: could not fetch SPY quote** | Network issue or Yahoo rate-limited you; wait a minute or check connectivity |
| Dashboard opens but **Recommendations** is empty | Run a scan: click "Run scan now" (or `sentinel scan` from terminal). After-hours scans typically yield 0 recs because intraday signals don't have current bars to fire on. |
| `FileNotFoundError: config/default.yaml` | Run from the `sentinel/` directory, or set `SENTINEL_CONFIG=/path/to/default.yaml` |
| Streamlit warns about `xcode-select --install` | Cosmetic — Watchdog speeds up file-change detection. Optional. |
| FinBERT errors on first run | The download takes a few minutes; subsequent runs use the cached model. If torch fails to install, try `pip install torch --index-url https://download.pytorch.org/whl/cpu` |
| Logs are huge | Rotation is daily, 14 days kept (`logs/sentinel.jsonl*`). Delete old files freely. |

---

## Roadmap (Phase 2 — explicit go-ahead required)

1. `AlpacaBroker` implementing the same `BrokerPort` (paper sandbox first).
2. Triple-confirmation flow before live mode (env var + CLI flag + typed account number).
3. Native Alpaca stop / trailing-stop / MOC-flatten orders (replaces local sweep).
4. Out-of-process watchdog + macOS launchd for heartbeat-flatten.
5. Kill-switch CLI + macOS global hotkey.
6. Backtest harness running the same `StrategyPipeline` against historical bars (already scaffolded in design; not built in Phase 1).

None of this happens until the user is satisfied with weeks of virtual-portfolio results and explicitly approves moving to Phase 2.

---

## License

Proprietary. Single user.
