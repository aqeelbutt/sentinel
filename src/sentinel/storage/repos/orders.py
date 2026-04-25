"""Virtual order persistence — used for idempotency (client_order_id dedupe)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sentinel.storage.db import connect


def get(db_path: Path, client_order_id: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM virtual_orders WHERE client_order_id = ?",
            (client_order_id,),
        ).fetchone()
    return dict(row) if row else None


def insert_filled(
    db_path: Path,
    *,
    client_order_id: str,
    symbol: str,
    side: str,
    qty: int,
    order_type: str,
    limit_price: Decimal | None,
    submitted_at: datetime,
    fill_price: Decimal,
    fill_ts: datetime,
    position_id: str | None,
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO virtual_orders
                (client_order_id, symbol, side, qty, order_type, limit_price,
                 submitted_at, status, fill_price, fill_ts, position_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'filled', ?, ?, ?)
            """,
            (
                client_order_id,
                symbol,
                side,
                qty,
                order_type,
                str(limit_price) if limit_price is not None else None,
                submitted_at.astimezone(timezone.utc).isoformat(),
                str(fill_price),
                fill_ts.astimezone(timezone.utc).isoformat(),
                position_id,
            ),
        )
        conn.commit()
