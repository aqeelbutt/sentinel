# Sentinel — How-To

Practical step-by-step for launching, running, and operating Sentinel.
For architecture and design rationale see [`CLAUDE.md`](CLAUDE.md). For the
full feature list and history see [`CHANGELOG.md`](CHANGELOG.md).

Repo lives at `/Users/aqeelbutt/Downloads/NEW/sentinel`.

---

## TL;DR (already-set-up users)

```bash
cd /Users/aqeelbutt/Downloads/NEW/sentinel
source .venv/bin/activate
sentinel dashboard          # opens http://127.0.0.1:8501
```

That's it. Everything else below is for first-time setup, advanced usage, and troubleshooting.

---

## 1. First-time setup (one-time)

### Install Python venv + sentinel package

```bash
cd /Users/aqeelbutt/Downloads/NEW/sentinel

# create the virtual environment (one time only)
python3 -m venv .venv
source .venv/bin/activate

# install sentinel + dev/test dependencies
pip install --upgrade pip
pip install -e ".[dev]"
```

### Verify it works

```bash
sentinel doctor
```

Expected output: all rows `OK` except possibly `api_keys` (we'll fix that next).

```
                       Sentinel health check
┏━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Check    ┃ Status ┃ Detail                                     ┃
┡━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ python   │ OK     │ 3.12.5                                     │
│ imports  │ OK     │ all 12 core imports OK                     │
│ config   │ OK     │ loaded; mode=virtual, watchlist=13 symbols │
│ storage  │ OK     │ db at data/sentinel.db (schema v1)         │
│ yfinance │ OK     │ SPY last=712.99                            │
│ logs     │ OK     │ logs                                       │
│ nlp      │ OK     │ backend=stub (FinBERT not required)        │
│ api_keys │ OK     │ keys present for: newsapi, finnhub         │
└──────────┴────────┴────────────────────────────────────────────┘
```

---

## 2. API keys (one-time, stored in macOS Keychain)

Each key is stored in macOS Keychain — never in any file. You can also use
the `.env` fallback (see `.env.example`) if you'd rather not use Keychain.

### Required (already set)
```bash
sentinel keychain set finnhub <your-key>      # https://finnhub.io/dashboard
sentinel keychain set newsapi <your-key>      # https://newsapi.org/account
```

### For AI Analyst (Phase B — Claude analyst)
```bash
sentinel keychain set anthropic <your-key>    # https://console.anthropic.com
```

### Verify
```bash
sentinel keychain list
```

Should show ✅ next to each key with a masked preview.

---

## 3. Launch the dashboard

### From the terminal
```bash
cd /Users/aqeelbutt/Downloads/NEW/sentinel
source .venv/bin/activate
sentinel dashboard
```

Then open **http://127.0.0.1:8501** in your browser. Streamlit reloads
automatically when source files change.

### Sidebar navigation

| Page | What it shows |
|---|---|
| **Recommendations** | Live confluence-based recs (your watchlist + discovery universe) |
| **🧠 AI Analyst** | Claude-powered structured trade ideas with full data lineage |
| **📊 Market Pulse** | Top daily/weekly movers + news context for each |
| **📅 Catalyst Calendar** | Next 7 days of earnings, IPOs, macro events; flags blackouts |
| **Open positions** | Current positions with mark, unrealized P&L, time held, distance to TP/stop |
| **Closed trades** | Full audit trail: bought-at, sold-at, R-multiple, close reason, source rec |
| **Performance** | Equity curve + per-window metrics vs SPY |
| **Backtests** | Persisted backtest runs ranked by Sharpe; per-symbol/per-signal breakdowns |
| **Watchlist** | Add/remove tickers from the scan universe |
| **Data sources** | API key status + per-source article counts (1h/4h/24h) |
| **Audit log** | Every decision the system has made (filterable) |

### Sidebar Quick Actions card
- 🔄 **Run scan** — trigger a confluence scan now
- 🧠 **AI scan** — call Claude analyst (requires Anthropic key)
- ⚖️ **Sweep positions** — force profit-take/stop check on open positions
- ➕ **Add ticker** — add a symbol to watchlist without leaving the dashboard

### Sidebar shows live ET clock + NYSE OPEN/CLOSED status at the top.

---

## 4. Always-on supervisor (recommended for active trading)

This makes Sentinel run continuously in the background — scans + sweeps every
5 minutes during market hours, restarts automatically on crash, starts at
every login. **Already installed and running** if you've followed this guide.

### Install / start
```bash
bash scripts/install-launchd.sh install
```

### Check status
```bash
bash scripts/install-launchd.sh status
# ✅ com.sentinel.run is loaded:
# -	1	com.sentinel.run
```

### Tail live logs
```bash
bash scripts/install-launchd.sh logs
```

### Stop / uninstall
```bash
bash scripts/install-launchd.sh uninstall
```

Fully reversible — removes the loaded service and the plist file. No
system-wide changes.

### What it actually does
- Reads `scripts/com.sentinel.run.plist`
- Copies it to `~/Library/LaunchAgents/com.sentinel.run.plist`
- Runs `launchctl load -w` on it (starts immediately + at every login)
- Process: `.venv/bin/sentinel run --interval 5m`
- Restarts on crash (30s throttle so it doesn't spin)
- Logs to `logs/launchd-stdout.log` and `logs/launchd-stderr.log`

---

## 5. CLI cheatsheet

### Ops / health
```bash
sentinel doctor                              # health check (all subsystems)
```

### Scanning + AI
```bash
sentinel scan                                # one-off confluence scan
sentinel ai-scan                             # one-off AI analyst (Claude)
sentinel ai-scan --model claude-opus-4-7     # higher quality, higher cost
sentinel run --interval 5m                   # supervisor loop (or use launchd)
sentinel sweep                               # force profit-take/stop sweep now
```

### Watchlist
```bash
sentinel watch list
sentinel watch add NVDA AVGO MU SMCI
sentinel watch remove TSLA
```

### Catalyst calendar
```bash
sentinel catalysts refresh                   # pull next 14 days from Finnhub
sentinel catalysts refresh --days-ahead 30
sentinel catalysts list --days 7             # show what's coming up
```

### Virtual portfolio (manual ops)
```bash
sentinel list                                # current positions + account state
sentinel add AAPL --qty 10                   # manual buy at last quote
sentinel close AAPL                          # close a position at market
```

### Performance + backtest
```bash
sentinel report --window 1w                  # live virtual portfolio metrics
sentinel report --window 1mo

sentinel backtest --start 2024-04-01 --end 2026-04-01 --timeframe 1d
sentinel backtest --start 2024-04-01 --end 2026-04-01 --variant combined --timeframe 1d
```

Variants: `baseline | trend_filter | atr_stops | gap_followthrough | combined`

### Keychain
```bash
sentinel keychain list
sentinel keychain set <name> <value>         # newsapi | finnhub | anthropic
sentinel keychain remove <name>
```

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `sentinel: command not found` | Activate the venv: `source .venv/bin/activate` |
| `sentinel doctor` shows `imports FAIL` | `pip install -e ".[dev]"` again from repo root |
| Dashboard says **API key not configured** for AI Analyst | `sentinel keychain set anthropic <key>` then refresh page |
| **No active recommendations** on the Recommendations page | Normal — confluence requires 2-of-3 signals to fire on the same bar with a clean regime. Click **Run scan** during market hours, add more volatile tickers like SMCI/COIN/MARA, or check **Audit log** for per-symbol rejection reasons |
| Catalyst Calendar shows **No catalysts cached** | Click **Refresh calendar** in the panel (or `sentinel catalysts refresh` in terminal) — needs Finnhub key |
| Streamlit warnings about `xcode-select --install` | Cosmetic — Watchdog speeds up file-change detection. Optional: `xcode-select --install` |
| `launchd-stderr.log` shows errors | `bash scripts/install-launchd.sh logs` to investigate; common cause is venv path moved |
| Dashboard doesn't show new code changes | Cmd-R to refresh; if that doesn't work, kill the streamlit process and `sentinel dashboard` again. Streamlit caches modules at startup. |
| `sentinel run` from terminal can't bind port | The launchd version is already running. Check with `bash scripts/install-launchd.sh status` |

### Logs to look at when things misbehave
- `logs/sentinel.jsonl` — structured JSON of every decision the engine made
- `logs/launchd-stdout.log` — output of the always-on supervisor
- `logs/launchd-stderr.log` — errors from the supervisor
- Dashboard → **Audit log** page — same decisions, filterable + searchable

---

## 7. Daily usage pattern (recommended)

Once everything's set up, your day looks like this:

**Morning, before open (9:00 ET-ish)**
1. Open dashboard
2. Click 🧠 **AI scan** in Quick Actions sidebar (or wait for the 9:30 auto-scan)
3. Skim **Catalyst Calendar** — anything you have a position in with earnings today?
4. Skim **Market Pulse → Daily movers** — pre-market context

**During market hours**
- launchd is already scanning + sweeping every 5 minutes; positions auto-close on profit-take or stop
- Dashboard's sidebar shows live ET clock + 🟢 OPEN status
- Open the **Recommendations** page when you want to consider new entries
- Each rec card shows: signals fired, sentiment, R:R, suggested qty, RiskManager decision, **forecast from backtest history** for the same signal mix
- Click **➕ Add to virtual portfolio** if you want to take a setup

**End of day / weekend**
- Check **Closed trades** for today's activity
- Check **Performance** for week/month vs SPY
- Run a `sentinel backtest --variant combined` if you've changed strategy params and want to see the impact

---

## 8. What's where in the repo

```
NEW/sentinel/
├── CLAUDE.md                full architecture + design context
├── README.md                project overview
├── HOWTO.md                 ← you are here
├── CHANGELOG.md             every feature added, with reasoning
├── .env.example             template for API keys (Keychain is preferred)
├── pyproject.toml           dependencies (pip install -e ".[dev]")
├── config/default.yaml      everything tunable
├── .streamlit/config.toml   dashboard theme
├── scripts/
│   ├── com.sentinel.run.plist     launchd config
│   └── install-launchd.sh         install/uninstall always-on
├── src/sentinel/
│   ├── core/                domain types + MarketClock (ET-aware)
│   ├── data/                yfinance + news + catalysts + macro regime
│   ├── intelligence/        Claude AI Analyst
│   ├── sentiment/           NLP backends + tiered weighting
│   ├── strategy/            signals + confluence + scanner
│   ├── risk/                RiskManager + every gate
│   ├── execution/           VirtualBroker + layered exit logic
│   ├── analytics/           performance metrics
│   ├── backtest/            harness + variants + report
│   ├── dashboard/           Streamlit panels
│   ├── ops/                 logging + keychain + health
│   └── cli.py               typer CLI
├── tests/unit/              35 tests, all passing
├── data/                    SQLite DB + cached bars (gitignored)
└── logs/                    JSON logs + launchd logs (gitignored)
```
