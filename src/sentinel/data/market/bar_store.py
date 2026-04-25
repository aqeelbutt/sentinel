"""Bar store: read-through cache against price_history table.

Caller asks for bars; we serve from SQLite if fresh, otherwise fetch from
yfinance, persist, and return. Freshness threshold is timeframe-dependent
(stale 5m bars are useless for live signals; stale daily bars are fine).
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sentinel.core.types import Bar
from sentinel.data.market import yfinance_feed
from sentinel.storage.repos import price_history


_FRESHNESS = {
    "1m": timedelta(minutes=2),
    "5m": timedelta(minutes=10),
    "15m": timedelta(minutes=30),
    "1h": timedelta(hours=2),
    "1d": timedelta(hours=24),
}


def get_bars(
    db_path: Path,
    symbol: str,
    timeframe: str,
    *,
    days: int,
    force_refresh: bool = False,
) -> list[Bar]:
    """Return up to `days` of recent bars for `symbol` at `timeframe`."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    if not force_refresh:
        cached = price_history.fetch_range(
            db_path, symbol=symbol, timeframe=timeframe, start=start, end=end,
        )
        if cached and _is_fresh(cached, timeframe):
            return [_row_to_bar(r) for r in cached]

    fresh = yfinance_feed.fetch_bars(symbol, timeframe, days=days)
    if fresh:
        price_history.upsert_many(db_path, [asdict(b) for b in fresh])
    return fresh


def _is_fresh(rows: list[dict], timeframe: str) -> bool:
    if not rows:
        return False
    last_ts = datetime.fromisoformat(rows[-1]["ts"])
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last_ts) < _FRESHNESS.get(timeframe, timedelta(minutes=10))


def _row_to_bar(r: dict) -> Bar:
    from decimal import Decimal
    ts = datetime.fromisoformat(r["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return Bar(
        symbol=r["symbol"],
        ts=ts,
        open=Decimal(r["open"]),
        high=Decimal(r["high"]),
        low=Decimal(r["low"]),
        close=Decimal(r["close"]),
        volume=int(r["volume"]),
        timeframe=r["timeframe"],
    )
