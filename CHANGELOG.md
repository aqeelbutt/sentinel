# Changelog

All notable changes to Sentinel. Newest first.

The format roughly follows [Keep a Changelog](https://keepachangelog.com/) but
without strict version numbers — this is a single-user research repo.

---

## [Unreleased] — 2026-04-25 — AI Analyst + always-on launchd

### Added
- **🧠 Claude AI Analyst integration** (`sentinel/intelligence/`):
  - `snapshot.py` — assembles structured market snapshot for Claude (top movers,
    regime, catalysts next 7d, headlines per mover, portfolio state)
  - `claude_analyst.py` — Anthropic SDK wrapper with strict JSON-output system
    prompt, tolerant parser, **idempotent within 5min** on identical snapshots
    (no spend on repeat clicks)
  - `types.py` — `AIRecommendation` dataclass with thesis, conviction, catalysts
    cited, risk factors, **inputs lineage**, cost
- **AI Analyst dashboard panel** — card per recommendation with thesis,
  conviction emoji, catalysts, risk factors, full data lineage, raw model
  response, and Add-to-Portfolio button (passes the same RiskManager gates).
- **`sentinel ai-scan` CLI** + 🧠 AI scan button in Quick Actions sidebar.
- **`ai_recommendations` SQLite table** with idempotency hash.
- **Anthropic API key** via Keychain (`sentinel keychain set anthropic`) with
  `ANTHROPIC_API_KEY` env-var fallback.
- **Always-on supervisor via macOS launchd**:
  - `scripts/com.sentinel.run.plist` — KeepAlive + RunAtLoad
  - `scripts/install-launchd.sh` — install / uninstall / status / logs
  - Runs `sentinel run --interval 5m` continuously, restarts on crash, starts
    at every login. Logs to `logs/launchd-stdout.log`.
- **ET time clock on sidebar** — visible current Eastern Time so the user
  always sees market-local time.

### Changed
- `catalysts.next_event_for()` accepts a `now=` kwarg for fixed-clock testing.
- Default AI model: `claude-sonnet-4-6`. Switchable via `--model` CLI flag.

### ET convention (made explicit)
- **Internal storage**: UTC ISO8601 strings (avoids DST chaos in DB).
- **All UI display**: converted to America/New_York via `fmt_ts` helper.
- **MarketClock.now()**: returns ET-tz datetime; all `is_market_open` and exit-
  window math runs against ET. Already in place since v0.1; now also surfaced
  in the dashboard sidebar.

### Cost note
Sonnet ~$0.005-0.020 per AI scan, Opus ~$0.020-0.080. Recommend running AI
scans on demand (Quick Actions button) rather than every 5min `sentinel run`
cycle, unless you want continuous narrative.

---

## [Unreleased] — 2026-04-24 (evening) — Phase A: Catalyst Calendar

### Added
- **Catalyst calendar** (`sentinel/data/catalysts.py` + `storage/repos/catalysts.py`).
  Pulls earnings + IPO + macro events from Finnhub `/calendar/earnings` and
  `/calendar/ipo` (one HTTP request covers a date range across all symbols).
  Falls back to the static FOMC/CPI/NFP calendar when no Finnhub key is set.
- **EARNINGS_BLACKOUT risk gate** in `RiskManager`. Refuses new entries within
  `risk.earnings_blackout_hours` (default 48h) of a symbol's next scheduled
  earnings — earnings are binary risk and account for a large share of unforced
  day-trader losses.
- **Catalyst Calendar dashboard panel** — 7-day view grouped by date,
  color-coded by proximity, flags symbols already in open positions.
- **`sentinel catalysts refresh / list` CLI commands**.
- **Quick Actions sidebar card** — one-click access to Run scan · Refresh catalysts ·
  Sweep positions · Add to watchlist. Reduces friction for routine ops.
- 2 new unit tests for EARNINGS_BLACKOUT (35 tests total).

### Changed
- New `Gate.EARNINGS_BLACKOUT` slotted between `PRICE_SANITY` and `CORRELATION`.
- New schema table `catalysts` with unique index on (symbol, type, date); auto-migrated.

---

## [Unreleased] — 2026-04-24 (afternoon)

### Added
- **Layered exit logic** (`virtual_broker.sweep_open_positions` + backtest harness) —
  priority order: profit-take (+5%) → trailing stop (active above +1.5%, trails 0.75%
  below peak) → break-even stop (active once peak >= +1%, exits at entry) →
  hard stop (-1%). Tracks `peak_price` per position. **Once a trade goes +1%
  in your favor, the break-even stop ensures it can never close at a loss.**
  Backtest result: combined+layered hits 54% win rate, -3.43% max DD.
- **`peak_price` column** on `virtual_positions` (auto-migrated for existing DBs)
  + `positions.update_peak()` repo method.
- **Discovery universe** (`sentinel/data/universe.py`) — curated ~150 liquid US
  equities. Scanner now scans **watchlist + discovery** so Sentinel can recommend
  names beyond the user's pre-selected list. Discovery recs tagged `[discovery]`
  in the rationale. Config flags: `watchlist.discovery_enabled`, `watchlist.discovery_max_recs`.
- **Streamlit theme + branded logo** (`.streamlit/config.toml` + custom CSS in
  `dashboard/app.py`) — gradient SENTINEL logo, gradient primary buttons with
  hover lift, dark navy palette, hover-highlight on cards.
- **Empty-state diagnostic on Recommendations** — when no recs, shows the
  macro regime status, per-symbol breakdown of fired signals, and concrete
  suggestions (add tickers / wait for market hours / tune threshold).
- **`.env` auto-load via `python-dotenv`** — API keys can live in `.env`
  (gitignored) as a fallback to macOS Keychain. Useful for servers / CI / non-mac.
- 2 new unit tests for layered exit (33 tests total).

### Changed
- **Repo relocated** from `/Users/aqeelbutt/Downloads/sentinel` to
  `/Users/aqeelbutt/Downloads/NEW/sentinel` (the latter is git-init'd for GitHub).
- **VirtualBroker sweep** walks the full layered exit ladder using each
  position's own peak_price; backtest harness mirrors it.

---

## [Unreleased] — 2026-04-24 (morning)

### Added
- **Backtest harness** (`sentinel/backtest/`) — replays historical bars through
  the same signal/risk/exit logic as live, produces a `BacktestReport` with
  Sharpe, Sortino, max drawdown, win rate, profit factor, and SPY-relative
  metrics. CLI: `sentinel backtest --start … --end … [--variant …]`.
- **Strategy variants** (`sentinel/backtest/variants.py`) — research-backed
  augmentations to the baseline strategy, each toggleable per-backtest:
  - `trend_filter` — long entries gated by price > SMA(N)
  - `atr_stops` — adaptive stops/targets at multiples of ATR(14)
  - `gap_followthrough` — Gap-and-Go requires the post-gap bar to confirm
  - `combined` — all three above
- **Backtest persistence** — `backtest_trades` SQLite table; every backtest
  run is saved and queryable from the dashboard.
- **Recommendations forecast** — each rec card now shows historical win rate /
  expectancy / avg P&L for trades with the same signal mix, sourced from prior
  backtest data.
- **Card-based Recommendations panel** — bordered cards per rec instead of a
  dense table; signals as badges, R:R metric, full audit drawer.
- **Backtests dashboard panel** — every persisted run side-by-side, drill into
  a run for per-symbol and per-signal-mix breakdowns.
- **Per-trade audit on Closed Trades** — expander per trade with rec→fill
  latency, R-multiple, full source-rec lineage, audit JSON.
- **Per-position detail on Open Positions** — distance-to-profit-take,
  distance-to-stop, $-at-risk, time held, full source-rec lineage.
- **Data sources panel** — confirms API keys are wired and shows article
  counts per source over 1h/4h/24h windows.
- **`sentinel run` supervisor loop** — `sentinel run --interval 5m` scans on
  a schedule, sweeps open positions, respects market hours.
- **`sentinel keychain`** — manage API keys via macOS Keychain
  (`set/list/remove`) with env-var fallback.
- **NewsAPI + Finnhub integration** — paid-API news fetchers wired through
  the same canonical-source attribution pipeline.
- **`sentinel doctor`** — pre-flight health checks (Python version, imports,
  config, DB, yfinance, logs, NLP backend, API keys).

### Changed
- **Source canonicalization** — upstream source names (NewsAPI / Finnhub
  return mixed-case publisher names) are normalized to a canonical lowercase
  key (`reuters`, `seeking_alpha`, `cnbc`, …) so tier weighting works.
- **News sources defaults** — Reuters RSS removed (their public RSS was
  shut down in 2020); CNBC, MarketWatch, Yahoo RSS verified working;
  EDGAR + Finnhub + NewsAPI added to defaults.
- **httpx logging silenced** at INFO level to prevent API keys leaking
  into logs through URL query strings; Finnhub token now sent via header.
- **Per-symbol scan diagnostics** — `sentinel scan` prints one line per
  symbol showing fired signals + sentiment + reason for rejection, so
  "0 recommendations" is self-diagnosing.
- **Gap-and-Go signal bug fix** — was comparing yesterday's close to a bar
  from 10 days ago because intraday history wasn't filtered to today.
- **Streamlit `use_container_width`** migrated to `width="stretch"` to silence
  deprecation warnings.

### Security
- API keys never written to config files. Stored in macOS Keychain (service
  `sentinel`) via the `keyring` library, with env-var fallback for CI.
- httpx INFO-level URL logging silenced to prevent query-string token leaks.

### Strategy results so far (2-year backtest, daily bars, 17-symbol watchlist)

| Variant | Return | Sharpe | Max DD | Win % | Verdict |
|---|---:|---:|---:|---:|---|
| baseline | +7.83% | 0.36 | -8.81% | 34% | LOSES_TO_SPY |
| trend_filter | +9.20% | 0.46 | -7.42% | 35% | LOSES_TO_SPY |
| atr_stops | +8.84% | 0.55 | -5.29% | 42% | LOSES_TO_SPY |
| gap_followthrough | +5.06% | 0.27 | -9.47% | 32% | LOSES_TO_SPY |
| **combined** | **+9.57%** | **0.69** | **-3.66%** | **43%** | **TIES_SPY** |
| SPY benchmark | +25.49% | 0.77 | -19.00% | — | — |

The `combined` variant nearly doubles Sharpe vs baseline (0.36 → 0.69) and
cuts max drawdown by 60% (-8.81% → -3.66%). On a risk-adjusted basis it now
ties SPY; on absolute return it still trails by ~16pp. Intraday backtests
(when we get a paid intraday data source) are expected to improve this further.

---

## [0.1.0] — 2026-04-23 — Phase 1 scaffolding

### Added
- Initial project skeleton: `sentinel.core` (types, MarketClock, identity),
  `sentinel.storage` (SQLite + repos), `sentinel.data` (yfinance + macro
  regime + news aggregator), `sentinel.sentiment` (NLPBackend protocol +
  FinBERT/stub), `sentinel.strategy` (signals + confluence + scanner),
  `sentinel.risk` (RiskManager + sizing + correlation), `sentinel.execution`
  (BrokerPort + VirtualBroker), `sentinel.dashboard` (Streamlit panels),
  `sentinel.cli` (typer commands).
- `MarketClock` with full NYSE calendar + DST + early-close handling.
- `RiskManager` with all gates (daily/weekly loss, concurrent/per-position/
  deployed caps, correlation, PDT, circuit breakers).
- `VirtualBroker` with profit-take / hard-stop sweep, deterministic
  client-order-id idempotency.
- 31 unit tests covering MarketClock, sizing, sentiment decay,
  VirtualBroker fills + sweep, RiskManager gates.
- Streamlit dashboard with Recommendations / Open Positions / Closed
  Trades / Performance / Watchlist / Audit log panels.
- Project decision: **Phase 1 = virtual portfolio only, no broker**.
  Phase 2 (Alpaca paper → live with triple-confirm) explicitly deferred
  until the strategy demonstrates an edge in virtual money.
