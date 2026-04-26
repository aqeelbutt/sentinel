-- Sentinel SQLite schema. Single migration for v0.1.
-- Conventions:
--   * Decimal-typed values stored as TEXT to preserve precision (Decimal(str)).
--   * Timestamps stored as ISO8601 UTC strings.
--   * decisions_log is append-only — never UPDATE or DELETE rows here.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendations (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    score REAL NOT NULL,
    entry_price_ref TEXT NOT NULL,
    suggested_stop TEXT NOT NULL,
    suggested_take_profit TEXT,
    suggested_qty INTEGER NOT NULL,
    signals_fired TEXT NOT NULL,
    sentiment_score REAL,
    regime_hostile INTEGER,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    full_payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rec_created ON recommendations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rec_symbol ON recommendations(symbol);

CREATE TABLE IF NOT EXISTS virtual_positions (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    avg_entry_price TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    stop_price TEXT,
    take_profit TEXT,
    peak_price TEXT,                  -- highest mark seen since entry; updated on each sweep
    status TEXT NOT NULL,
    closed_at TEXT,
    exit_price TEXT,
    realized_pnl TEXT,
    realized_pnl_pct REAL,
    close_reason TEXT,                -- hard_stop | breakeven_stop | trailing_stop | profit_take | user
    source_recommendation_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_pos_status ON virtual_positions(status);
CREATE INDEX IF NOT EXISTS idx_pos_symbol ON virtual_positions(symbol);

CREATE TABLE IF NOT EXISTS virtual_orders (
    client_order_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    order_type TEXT NOT NULL,
    limit_price TEXT,
    submitted_at TEXT NOT NULL,
    status TEXT NOT NULL,
    fill_price TEXT,
    fill_ts TEXT,
    position_id TEXT
);

CREATE TABLE IF NOT EXISTS decisions_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    symbol TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dec_ts ON decisions_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_dec_kind ON decisions_log(kind);
CREATE INDEX IF NOT EXISTS idx_dec_symbol ON decisions_log(symbol);

CREATE TABLE IF NOT EXISTS sentiment_cache (
    article_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    raw_score REAL NOT NULL,
    scored_at TEXT NOT NULL,
    source TEXT NOT NULL,
    published_at TEXT NOT NULL,
    title TEXT,
    symbols TEXT NOT NULL,
    PRIMARY KEY (article_id, model_name)
);
CREATE INDEX IF NOT EXISTS idx_sc_published ON sentiment_cache(published_at DESC);

CREATE TABLE IF NOT EXISTS price_history (
    symbol TEXT NOT NULL,
    ts TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    close TEXT NOT NULL,
    volume INTEGER NOT NULL,
    PRIMARY KEY (symbol, ts, timeframe)
);
CREATE INDEX IF NOT EXISTS idx_ph_symbol_tf ON price_history(symbol, timeframe, ts DESC);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    snapshot_date TEXT PRIMARY KEY,
    equity TEXT NOT NULL,
    cash TEXT NOT NULL,
    deployed TEXT NOT NULL,
    open_positions INTEGER NOT NULL,
    spy_close TEXT
);

CREATE TABLE IF NOT EXISTS ai_recommendations (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    thesis TEXT NOT NULL,
    catalysts_cited TEXT,           -- JSON array
    conviction TEXT NOT NULL,       -- low | medium | high
    risk_factors TEXT,              -- JSON array
    time_horizon TEXT NOT NULL,     -- intraday | swing | position
    inputs_used TEXT NOT NULL,      -- JSON array of data points the model cited
    inputs_hash TEXT NOT NULL,      -- sha256 of the snapshot fed in (dedupe + audit)
    model TEXT NOT NULL,            -- model id, e.g. claude-sonnet-4-6
    cost_usd REAL,                  -- best-effort token cost
    raw_response TEXT,              -- full model output for audit
    created_at TEXT NOT NULL,
    acted_on INTEGER DEFAULT 0,     -- 1 if user added to portfolio
    related_position_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_created ON ai_recommendations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_symbol ON ai_recommendations(symbol);
CREATE INDEX IF NOT EXISTS idx_ai_inputs_hash ON ai_recommendations(inputs_hash);

CREATE TABLE IF NOT EXISTS catalysts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    catalyst_type TEXT NOT NULL,        -- earnings | ipo | dividend | split | macro
    event_date TEXT NOT NULL,           -- YYYY-MM-DD (date the event applies to)
    event_time TEXT,                    -- bmo | amc | dmh (during market hours) | NULL
    title TEXT,
    payload TEXT,                       -- JSON: extra fields (eps_estimate, hour, quarter, …)
    fetched_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cat_unique ON catalysts(symbol, catalyst_type, event_date);
CREATE INDEX IF NOT EXISTS idx_cat_date ON catalysts(event_date);
CREATE INDEX IF NOT EXISTS idx_cat_symbol ON catalysts(symbol);

CREATE TABLE IF NOT EXISTS halts (
    scope TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    halted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    config_hash TEXT,
    symbol TEXT NOT NULL,
    qty INTEGER NOT NULL,
    entry_price TEXT NOT NULL,
    exit_price TEXT NOT NULL,
    entry_ts TEXT NOT NULL,
    exit_ts TEXT NOT NULL,
    realized_pnl TEXT NOT NULL,
    realized_pnl_pct REAL NOT NULL,
    close_reason TEXT NOT NULL,
    signals TEXT NOT NULL,           -- comma-separated, sorted, e.g. "gap_and_go,vwap_reclaim"
    timeframe TEXT NOT NULL,
    backtest_start TEXT NOT NULL,
    backtest_end TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bt_signals ON backtest_trades(signals);
CREATE INDEX IF NOT EXISTS idx_bt_run ON backtest_trades(run_id);
