"""Pre-flight health checks. Used by `sentinel doctor`.

Checks:
  * Python version >= 3.11
  * All required imports resolve
  * Config loads without error
  * SQLite DB writable
  * yfinance can fetch a benchmark quote
  * (optional) FinBERT extra is installed if backend=finbert
  * Logs directory writable
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def run_all() -> list[CheckResult]:
    results: list[CheckResult] = []
    results.append(_check_python())
    results.append(_check_imports())
    results.append(_check_config())
    results.append(_check_db())
    results.append(_check_yfinance())
    results.append(_check_logs_dir())
    results.append(_check_finbert_if_configured())
    results.append(_check_api_keys_if_enabled())
    return results


def _check_python() -> CheckResult:
    if sys.version_info < (3, 11):
        return CheckResult("python", False, f"Need Python ≥ 3.11; have {sys.version.split()[0]}")
    return CheckResult("python", True, sys.version.split()[0])


def _check_imports() -> CheckResult:
    needed = [
        "yfinance", "pandas", "numpy", "pandas_market_calendars",
        "streamlit", "plotly", "structlog", "pydantic", "yaml",
        "feedparser", "httpx", "typer",
    ]
    missing = []
    for mod in needed:
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        return CheckResult("imports", False, f"Missing: {', '.join(missing)}. Run: pip install -e '.[dev]'")
    return CheckResult("imports", True, f"all {len(needed)} core imports OK")


def _check_config() -> CheckResult:
    try:
        from sentinel.core.config import load_config
        cfg = load_config()
        return CheckResult("config", True, f"loaded; mode={cfg.app.mode}, watchlist={len(cfg.watchlist.symbols)} symbols")
    except Exception as e:  # noqa: BLE001
        return CheckResult("config", False, str(e))


def _check_db() -> CheckResult:
    try:
        from sentinel.core.config import load_config
        from sentinel.storage.db import init_db
        cfg = load_config()
        init_db(cfg.storage.db_path, wal=cfg.storage.wal)
        with sqlite3.connect(cfg.storage.db_path) as conn:
            row = conn.execute("SELECT version FROM schema_version").fetchone()
        return CheckResult("storage", True, f"db at {cfg.storage.db_path} (schema v{row[0]})")
    except Exception as e:  # noqa: BLE001
        return CheckResult("storage", False, str(e))


def _check_yfinance() -> CheckResult:
    try:
        from sentinel.data.market.yfinance_feed import latest_quote
        q = latest_quote("SPY")
        if q is None:
            return CheckResult("yfinance", False, "could not fetch SPY quote — network or rate-limit issue")
        return CheckResult("yfinance", True, f"SPY last={q.last}")
    except Exception as e:  # noqa: BLE001
        return CheckResult("yfinance", False, str(e))


def _check_logs_dir() -> CheckResult:
    try:
        from sentinel.core.config import load_config
        cfg = load_config()
        cfg.logging.json_file.parent.mkdir(parents=True, exist_ok=True)
        return CheckResult("logs", True, str(cfg.logging.json_file.parent))
    except Exception as e:  # noqa: BLE001
        return CheckResult("logs", False, str(e))


def _check_api_keys_if_enabled() -> CheckResult:
    """Verify Keychain/env has keys for any enabled paid news source."""
    try:
        from sentinel.core.config import load_config
        from sentinel.ops.keychain import get_secret
        cfg = load_config()
        enabled = cfg.sentiment.news_sources.enabled
        missing: list[str] = []
        for src in ("newsapi", "finnhub"):
            if src in enabled and not get_secret(src):
                missing.append(src)
        if missing:
            return CheckResult(
                "api_keys", False,
                f"Enabled but no key: {', '.join(missing)}. Run: sentinel keychain set <name> <key>",
            )
        active = [s for s in ("newsapi", "finnhub") if s in enabled]
        if not active:
            return CheckResult("api_keys", True, "no paid-API sources enabled")
        return CheckResult("api_keys", True, f"keys present for: {', '.join(active)}")
    except Exception as e:  # noqa: BLE001
        return CheckResult("api_keys", False, str(e))


def _check_finbert_if_configured() -> CheckResult:
    try:
        from sentinel.core.config import load_config
        cfg = load_config()
        if cfg.sentiment.backend != "finbert":
            return CheckResult("nlp", True, f"backend={cfg.sentiment.backend} (FinBERT not required)")
        try:
            importlib.import_module("transformers")
            importlib.import_module("torch")
            return CheckResult("nlp", True, "FinBERT extra installed")
        except ImportError:
            return CheckResult("nlp", False, "backend=finbert but transformers/torch not installed; run `pip install -e '.[finbert]'`")
    except Exception as e:  # noqa: BLE001
        return CheckResult("nlp", False, str(e))
