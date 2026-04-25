"""Watchlist persistence."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sentinel.storage.db import connect


def add(db_path: Path, symbol: str) -> None:
    sym = symbol.upper().strip()
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist(symbol, added_at) VALUES (?, ?)",
            (sym, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def remove(db_path: Path, symbol: str) -> None:
    sym = symbol.upper().strip()
    with connect(db_path) as conn:
        conn.execute("DELETE FROM watchlist WHERE symbol = ?", (sym,))
        conn.commit()


def list_all(db_path: Path) -> list[str]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT symbol FROM watchlist ORDER BY symbol").fetchall()
    return [r["symbol"] for r in rows]


def replace_all(db_path: Path, symbols: list[str]) -> None:
    """Used by config seed on first run."""
    now = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        for s in symbols:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist(symbol, added_at) VALUES (?, ?)",
                (s.upper().strip(), now),
            )
        conn.commit()
