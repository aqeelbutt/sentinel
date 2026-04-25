"""Daily equity snapshots — the raw input to the Performance page."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sentinel.storage.db import connect


def upsert(
    db_path: Path,
    *,
    snapshot_date: date,
    equity: Decimal,
    cash: Decimal,
    deployed: Decimal,
    open_positions: int,
    spy_close: Decimal | None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO equity_snapshots
                (snapshot_date, equity, cash, deployed, open_positions, spy_close)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_date) DO UPDATE SET
                equity = excluded.equity,
                cash = excluded.cash,
                deployed = excluded.deployed,
                open_positions = excluded.open_positions,
                spy_close = excluded.spy_close
            """,
            (
                snapshot_date.isoformat(),
                str(equity),
                str(cash),
                str(deployed),
                open_positions,
                str(spy_close) if spy_close is not None else None,
            ),
        )
        conn.commit()


def list_since(db_path: Path, since: date) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM equity_snapshots WHERE snapshot_date >= ? ORDER BY snapshot_date",
            (since.isoformat(),),
        ).fetchall()
    return [dict(r) for r in rows]


def list_all(db_path: Path) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM equity_snapshots ORDER BY snapshot_date"
        ).fetchall()
    return [dict(r) for r in rows]
