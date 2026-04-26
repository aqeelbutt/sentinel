"""Catalyst calendar persistence — earnings dates, IPOs, etc.

Stored separately from sentiment_cache because catalysts are *scheduled
future events*, not historical articles. Powers two things:

  1. RiskManager.EARNINGS_BLACKOUT — refuse new entries within N hours of
     a symbol's earnings (binary risk).
  2. Catalyst dashboard panel — week-ahead view so the user knows which
     of their watchlist names are about to become unpredictable.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sentinel.storage.db import connect

NY = ZoneInfo("America/New_York")


def upsert_many(db_path: Path, events: list[dict[str, Any]]) -> int:
    """events: list of {symbol, catalyst_type, event_date (date), event_time, title, payload}."""
    if not events:
        return 0
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            e["symbol"].upper(),
            e["catalyst_type"],
            e["event_date"].isoformat() if hasattr(e["event_date"], "isoformat") else e["event_date"],
            e.get("event_time"),
            e.get("title", ""),
            json.dumps(e.get("payload", {})),
            fetched_at,
        )
        for e in events
    ]
    with connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO catalysts
              (symbol, catalyst_type, event_date, event_time, title, payload, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, catalyst_type, event_date) DO UPDATE SET
              event_time = excluded.event_time,
              title = excluded.title,
              payload = excluded.payload,
              fetched_at = excluded.fetched_at
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def list_upcoming(db_path: Path, days: int = 7, symbols: list[str] | None = None) -> list[dict[str, Any]]:
    """Events from today to today+days. Optional symbol filter."""
    today = date.today().isoformat()
    end = (date.today() + timedelta(days=days)).isoformat()
    sql = "SELECT * FROM catalysts WHERE event_date BETWEEN ? AND ?"
    args: list[Any] = [today, end]
    if symbols:
        placeholders = ",".join("?" for _ in symbols)
        sql += f" AND symbol IN ({placeholders})"
        args.extend(s.upper() for s in symbols)
    sql += " ORDER BY event_date, symbol"
    with connect(db_path) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def next_event_for(
    db_path: Path,
    symbol: str,
    catalyst_type: str = "earnings",
    horizon_days: int = 14,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Return the next catalyst datetime for `symbol` within `horizon_days`,
    or None. `now` defaults to wall-clock; pass an explicit datetime in tests
    so a fixed/virtual clock works correctly. Maps event_time string
    ('bmo' | 'amc' | NULL) to an approximate ET datetime.
    """
    base = (now.date() if now is not None else date.today())
    today = base.isoformat()
    end = (base + timedelta(days=horizon_days)).isoformat()
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT event_date, event_time FROM catalysts "
            "WHERE symbol = ? AND catalyst_type = ? "
            "  AND event_date BETWEEN ? AND ? "
            "ORDER BY event_date LIMIT 1",
            (symbol.upper(), catalyst_type, today, end),
        ).fetchone()
    if not row:
        return None
    event_date = date.fromisoformat(row["event_date"])
    return _to_event_dt(event_date, row["event_time"])


def _to_event_dt(event_date: date, event_time: str | None) -> datetime:
    """Convert (date, hour-hint) → tz-aware ET datetime."""
    et = (event_time or "").lower()
    if et == "bmo":         # before market open
        wall = time(7, 0)
    elif et == "amc":       # after market close
        wall = time(16, 30)
    elif et == "dmh":       # during market hours (rare)
        wall = time(12, 0)
    else:                   # unknown — treat as just-before-open conservatively
        wall = time(7, 0)
    return datetime.combine(event_date, wall, tzinfo=NY).astimezone(timezone.utc)


def list_for_dashboard(db_path: Path, days: int = 7) -> list[dict[str, Any]]:
    """All upcoming + add an `event_dt_iso` field for the panel."""
    rows = list_upcoming(db_path, days=days)
    for r in rows:
        r["event_dt_iso"] = _to_event_dt(date.fromisoformat(r["event_date"]), r["event_time"]).isoformat()
    return rows
