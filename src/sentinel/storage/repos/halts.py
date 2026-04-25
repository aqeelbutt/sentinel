"""Active risk halts so they survive process restart."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sentinel.storage.db import connect


def set_halt(db_path: Path, scope: str, reason: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO halts(scope, reason, halted_at) VALUES (?, ?, ?)",
            (scope, reason, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def clear_halt(db_path: Path, scope: str) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM halts WHERE scope = ?", (scope,))
        conn.commit()


def is_halted(db_path: Path, scope: str) -> bool:
    with connect(db_path) as conn:
        row = conn.execute("SELECT 1 FROM halts WHERE scope = ?", (scope,)).fetchone()
    return row is not None


def list_active(db_path: Path) -> list[dict[str, str]]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT scope, reason, halted_at FROM halts").fetchall()
    return [dict(r) for r in rows]
