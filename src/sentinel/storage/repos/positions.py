"""Virtual position persistence."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sentinel.core.types import Position, Side
from sentinel.storage.db import connect


def open_position(
    db_path: Path,
    *,
    symbol: str,
    side: Side,
    qty: int,
    avg_entry_price: Decimal,
    opened_at: datetime,
    stop_price: Decimal | None,
    take_profit: Decimal | None,
    source_recommendation_id: str | None,
) -> str:
    pos_id = str(uuid.uuid4())
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO virtual_positions
                (id, symbol, side, qty, avg_entry_price, opened_at,
                 stop_price, take_profit, status, source_recommendation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                pos_id,
                symbol,
                side.value,
                qty,
                str(avg_entry_price),
                opened_at.astimezone(timezone.utc).isoformat(),
                str(stop_price) if stop_price is not None else None,
                str(take_profit) if take_profit is not None else None,
                source_recommendation_id,
            ),
        )
        conn.commit()
    return pos_id


def close_position(
    db_path: Path,
    *,
    position_id: str,
    exit_price: Decimal,
    closed_at: datetime,
    realized_pnl: Decimal,
    realized_pnl_pct: float,
    close_reason: str,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE virtual_positions
               SET status = 'closed',
                   closed_at = ?,
                   exit_price = ?,
                   realized_pnl = ?,
                   realized_pnl_pct = ?,
                   close_reason = ?
             WHERE id = ? AND status = 'open'
            """,
            (
                closed_at.astimezone(timezone.utc).isoformat(),
                str(exit_price),
                str(realized_pnl),
                realized_pnl_pct,
                close_reason,
                position_id,
            ),
        )
        if conn.total_changes == 0:
            raise ValueError(f"No open position with id {position_id}")
        conn.commit()


def list_open(db_path: Path) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM virtual_positions WHERE status = 'open' ORDER BY opened_at"
        ).fetchall()
    return [dict(r) for r in rows]


def list_closed(db_path: Path, since: datetime | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        if since:
            rows = conn.execute(
                "SELECT * FROM virtual_positions WHERE status = 'closed' AND closed_at >= ? "
                "ORDER BY closed_at DESC",
                (since.astimezone(timezone.utc).isoformat(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM virtual_positions WHERE status = 'closed' ORDER BY closed_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def get_open_by_symbol(db_path: Path, symbol: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM virtual_positions WHERE status = 'open' AND symbol = ? LIMIT 1",
            (symbol,),
        ).fetchone()
    return dict(row) if row else None


def position_to_domain(row: dict[str, Any]) -> Position:
    return Position(
        symbol=row["symbol"],
        side=Side(row["side"]),
        qty=int(row["qty"]),
        avg_entry_price=Decimal(row["avg_entry_price"]),
        opened_at=datetime.fromisoformat(row["opened_at"]),
        stop_price=Decimal(row["stop_price"]) if row["stop_price"] else None,
        take_profit=Decimal(row["take_profit"]) if row["take_profit"] else None,
        source_recommendation_id=row["source_recommendation_id"],
    )


def update_peak(db_path: Path, position_id: str, new_peak: Decimal) -> None:
    """Update peak_price if `new_peak` exceeds the stored peak. No-op otherwise."""
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE virtual_positions "
            "SET peak_price = ? "
            "WHERE id = ? AND status = 'open' "
            "  AND (peak_price IS NULL OR CAST(peak_price AS REAL) < ?)",
            (str(new_peak), position_id, float(new_peak)),
        )
        conn.commit()
