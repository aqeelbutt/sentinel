"""Cached OHLCV bars from yfinance. Avoids hammering Yahoo on every UI refresh."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sentinel.storage.db import connect


def upsert_many(db_path: Path, bars: list[dict[str, Any]]) -> int:
    """bars: list of {symbol, ts (datetime), timeframe, open, high, low, close, volume}."""
    if not bars:
        return 0
    rows = [
        (
            b["symbol"],
            b["ts"].astimezone(timezone.utc).isoformat(),
            b["timeframe"],
            str(b["open"]),
            str(b["high"]),
            str(b["low"]),
            str(b["close"]),
            int(b["volume"]),
        )
        for b in bars
    ]
    with connect(db_path) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO price_history
                (symbol, ts, timeframe, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def fetch_range(
    db_path: Path,
    *,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM price_history
             WHERE symbol = ? AND timeframe = ? AND ts >= ? AND ts <= ?
             ORDER BY ts
            """,
            (
                symbol,
                timeframe,
                start.astimezone(timezone.utc).isoformat(),
                end.astimezone(timezone.utc).isoformat(),
            ),
        ).fetchall()
    return [dict(r) for r in rows]


def latest_close(db_path: Path, symbol: str, timeframe: str) -> Decimal | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT close FROM price_history WHERE symbol = ? AND timeframe = ? "
            "ORDER BY ts DESC LIMIT 1",
            (symbol, timeframe),
        ).fetchone()
    return Decimal(row["close"]) if row else None
