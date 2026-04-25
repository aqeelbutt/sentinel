# Sentinel — Guidance for Claude

This file is loaded automatically by Claude Code when working in this repo. Read it first before editing code.

---

## What this is

**Sentinel** is a locally-hosted Python engine that scans US equities/ETFs for trade setups using a "Triple-Check" confluence model (technical signals + weighted news sentiment + macro regime health) and tracks recommendations in a **virtual portfolio** (imaginary money) so the user can evaluate the strategy before risking real capital.

This is **Phase 1 of a two-phase project**. The project was originally specified as the "Autonomous Market Sentinel" (AMS) — a broker-connected autonomous trading engine via Alpaca. The user elected to start without any broker: Claude recommends stocks; the user manually adds them to a virtual portfolio via a UI; the system tracks performance over 1/2/3/4+ week windows against SPY. Broker integration is deferred to Phase 2 and will require the full triple-confirmation safety flow already designed in the original contracts.

> If the user asks "why no broker yet?": this is intentional. We are validating the strategy on paper-only data before connecting any real broker account. The port-based architecture (`BrokerPort` Protocol) means `AlpacaBroker` can drop in later without touching strategy or risk code.

---

## Phase 1 scope (what's in this repo NOW)

- **Recommendation engine**: Triple-Check pipeline produces `Recommendation` objects with score, rationale, and contributing signals/sentiment/regime.
- **Virtual portfolio / `VirtualBroker`**: fills at current quote, tracks positions, computes P&L. Implements the same `BrokerPort` Protocol that `AlpacaBroker` will implement in Phase 2.
- **Streamlit UI**: scanner page, portfolio page, performance page (1w / 2w / 3w / 1mo / 3mo / inception, each vs SPY), closed-trades page, audit log.
- **SQLite storage**: append-only decisions log, virtual positions, price history, sentiment cache, watchlist.
- **CLI** (`sentinel`): `run`, `scan`, `dashboard`, `list`, `price`, `add`, `close`, `report`.

### Explicitly NOT in Phase 1
- No `alpaca-py` calls (the package is listed as optional-extra but not wired).
- No live order submission, ever, in Phase 1 — there's no broker credential flow.
- No hotkey / launchd watchdog / heartbeat-flatten (those are Phase 2, when real money is at stake).
- No PDT check against a real broker account (the `pdt.py` module exists and is tested against synthetic fills so it's ready for Phase 2).

### Phase 2 preview (do NOT implement without explicit go-ahead)
- `AlpacaBroker` implementing `BrokerPort`.
- Triple-confirmation to enter live mode (env var + CLI flag + typed account number).
- Kill switch, out-of-process watchdog, macOS global hotkey.
- Native stop / trailing-stop / MOC flatten orders.

---

## Strategy summary (Triple-Check)

A recommendation requires **all three** of the following:

### 1. Macro regime gate (no long recs if hostile)
- SPY below 200-period VWAP on 5m chart → block
- SPY or QQQ down > 1.5% intraday → block
- VIX > 28 OR VIX up > 15% on day → block
- FOMC / CPI / NFP day: block until 30 min after release

### 2. Technical signals (≥ 2 must fire simultaneously)
- **Gap-and-Go**: overnight gap > 2% on relative volume > 2.0× 20-day ADV
- **Mean Reversion (long)**: price > 2.5σ below 20-period VWAP AND RSI(14) < 30
- **VWAP Reclaim**: price crosses above session VWAP with volume confirmation

Liquidity pre-filter: ADV > 1M shares, spread < 0.1%, price > $5.

### 3. Weighted sentiment
- Tier 1 (w=1.0): Reuters, Bloomberg, WSJ, SEC EDGAR, company PR
- Tier 2 (w=0.5): Benzinga, Seeking Alpha, CNBC, MarketWatch
- Tier 3 (w=0.1): Reddit (r/wsb, r/stocks), StockTwits, X
- Recency decay: full < 1h, 0.5× at 4h, 0× at 24h
- Qualifies only if: `score > 0.7` AND `≥ 3 independent sources` AND `newest article < 4h`
- Default NLP backend: **FinBERT** (ProsusAI/finbert) via `transformers`. Ship with a neutral stub backend (`StubNLPBackend`) enabled by default so the system runs without downloading 500MB of weights; FinBERT is an opt-in extra: `uv sync --extra finbert`.

---

## Risk model (still enforced in virtual mode — for realism)

Even though we're not touching a real broker, the `RiskManager` applies the same gates so that when we switch to Alpaca in Phase 2, results transfer cleanly.

- Fixed-fractional sizing: risk 0.5% of virtual equity per trade, sized by stop distance
- Max 5 concurrent positions · max 25% per position · max 40% deployed (60% cash floor)
- Daily loss limit: -2% of session-open equity halts new entries
- Weekly loss limit: -5% over rolling 5-session window halts all trading
- Correlation: reject new position if 30d correlation > 0.8 with an existing one
- Circuit breakers: 3 consecutive losing closed trades → halt day; > 20 orders / 5min → halt + alert

---

## Architecture (ports-and-adapters)

```
core ← storage ← {data, sentiment, risk, execution} ← strategy ← app
```

- `core/` — pure domain types and the `Clock` Protocol. No I/O.
- `execution/broker_port.py` — the `BrokerPort` Protocol. Both `VirtualBroker` (now) and `AlpacaBroker` (Phase 2) implement it.
- `strategy/pipeline.py` — the event loop body. Same code for live and backtest. Swaps `BrokerPort`, `Clock`, and `EventSource` at the edges.
- `ops/` — cross-cutting (logging, keychain). Imports only `core`.
- `dashboard/` — Streamlit, sync facade over the async core.
- `backtest/` — replay bars through the same `StrategyPipeline` with `VirtualBroker` + a `VirtualClock`.

### Layering rule (enforced)
Modules under `strategy/` and `risk/` **must not** import from `execution/`. Orders leave the system only via `app` wiring a `BrokerPort` into the pipeline. This is what keeps live and backtest equivalent.

### Idempotency
Every order carries a deterministic `client_order_id = sha256(session_id | symbol | bar_ts | intent_hash)[:16]`. `VirtualBroker` dedupes on this the same way `AlpacaBroker` will (Alpaca rejects duplicates server-side).

---

## Tech stack

- Python 3.11+ · async-first (asyncio) · `uv` for deps (pip fallback works)
- `typer` CLI · `pydantic` v2 config · `pyyaml`
- `streamlit` + `plotly` UI
- `yfinance` market data · `pandas-market-calendars` for NYSE calendar
- `aiosqlite` storage · WAL mode so CLI + Streamlit can coexist
- `structlog` JSON logs to `logs/sentinel.jsonl`
- `pytest` + `pytest-asyncio` tests
- Optional: `transformers` + `torch` behind the `finbert` extra

---

## How to run

```bash
cd /Users/aqeelbutt/Downloads/sentinel

# first time
uv sync                         # or: python3 -m venv .venv && source .venv/bin/activate && pip install -e .

# daily usage
sentinel dashboard              # opens Streamlit at http://localhost:8501
sentinel scan                   # run a one-off scan against the watchlist
sentinel list                   # print current virtual portfolio
sentinel report --window 1w     # P&L / Sharpe / vs-SPY for a window

# manage the watchlist
sentinel watch add AAPL MSFT NVDA
sentinel watch list
sentinel watch remove AAPL
```

FinBERT is opt-in because of its size/deps:
```bash
uv sync --extra finbert
# then in config/default.yaml set sentiment.backend: finbert
```

---

## Repo conventions

- **One responsibility per module.** If a module does two things, split it.
- **Use the `Clock` Protocol everywhere.** Never import `datetime.now()` inside strategy/risk/execution — it breaks backtests and tests. Always take a `Clock` parameter.
- **Frozen dataclasses for domain types**, `StrEnum` for finite sets. `Decimal` for money, never `float`.
- **`decisions_log` is append-only.** Every gate evaluation, signal firing, and sentiment score writes one row with full inputs. This is how we honestly evaluate the strategy later.
- **No feature flags or back-compat shims.** If a change breaks the schema, write a migration.
- **`sentinel/` is the package name.** Import as `from sentinel.core.clock import MarketClock`.

---

## User preferences (load-bearing)

1. **Review contracts before implementations.** For non-trivial components the user wants class signatures + docstrings + type hints, then approval, then code. The current contracts were reviewed and approved; significant new components should follow the same flow.
2. **Don't assume the edge is real.** After 30/60/90 days of virtual tracking, the user wants an honest report: does the strategy beat SPY buy-and-hold on Sharpe, Sortino, and max drawdown? If it doesn't, say so plainly. This is why `scripts/evaluate.py` + the Performance page always show SPY alongside portfolio metrics.
3. **Paper-first, forever, until explicitly told otherwise.** In Phase 1 this is trivial (no broker). In Phase 2 this is non-negotiable: the `LiveModeToken` type gate is a hard structural barrier.
4. **Concise responses, no padding.** When reporting what changed, say what changed. Don't restate the task.

---

## Current state (update this as the project evolves)

- **2026-04-23** — Phase 1 scaffolding created. Core, storage, clock, virtual broker, basic signals, Streamlit UI, CLI all present. FinBERT is stub-by-default. No broker integration. Ready for first `sentinel dashboard` smoke test.
- **2026-04-24** — News + paid APIs live (Finnhub + NewsAPI + EDGAR), backtest harness shipped, strategy variants A/B-tested. **Combined variant (trend filter + ATR stops + gap follow-through) ties SPY on Sharpe (0.69 vs 0.77) with -3.66% max DD vs SPY's -19%.** Live recs still use baseline strategy — promotion to combined requires applying the same filters in `strategy/scanner.py` (deferred until further validation). Card-based Recommendations UI shipped.
- **2026-04-24 (later)** — **Layered exit logic shipped** (priority order: profit-take → trailing stop → break-even stop → hard stop). Tracks `peak_price` per position; once a trade reaches +1% the floor moves to entry, so it can never go red afterward. Backtest with combined+layered: 54% win rate, -3.43% max DD. **Discovery universe** (~150 liquid US equities) added so the scanner recommends names beyond the user's watchlist (config: `watchlist.discovery_enabled`). **Streamlit theme + branded logo + gradient buttons + soft hover.** **Empty-state diagnostic**: when scan returns 0 recs, the UI shows a per-symbol breakdown of which signals fired and why none qualified. Repo relocated to `/Users/aqeelbutt/Downloads/NEW/sentinel`. `.env` auto-loaded for API keys (macOS Keychain still primary).

## Path note

Repo now lives at `/Users/aqeelbutt/Downloads/NEW/sentinel`. Earlier docs may
reference the older `/Users/aqeelbutt/Downloads/sentinel` path — adjust any
scripts accordingly.

## How the strategy is currently evaluated

Look at `sentinel backtest --variant combined --start 2024-04-01 --end 2026-04-01 --timeframe 1d`. That's the single source of truth for "is the strategy working?" Run it after any strategy change. Compare the Verdict line to the previous baseline. Anything that drops Sharpe is rejected; anything that raises it is promoted (eventually into the live scanner).

The Backtests page in the dashboard ranks every persisted run; the Recommendations cards now show forecast stats from prior backtest data, so each live recommendation is contextualized by what historically happened on the same signal mix.

---

## Open questions (carry forward to future sessions)

1. Bar timeframe for signals — proposing 5m as the default, 1m for VWAP-reclaim sensitivity. Confirm during first review of recommendations.
2. "Independent sources" semantics — proposing canonical-domain dedupe (Reuters via Yahoo = 1 source). Revisit after a week of news data.
3. Shorts disabled in Phase 1 (`shorts_enabled: false` in config). Revisit in Phase 2.
4. News ingestion: RSS for Reuters/Benzinga is functional without keys, but for quality/volume we'll likely need paid feeds. Defer until Phase 1 evaluation shows the rest of the pipeline works.
