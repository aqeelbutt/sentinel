"""Append-only decisions log. Every gate evaluation, signal firing,
sentiment score, and order intent writes one row.

This is the data we use to honestly evaluate the strategy after 30/60/90 days.
Never UPDATE or DELETE here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sentinel.storage.db import connect

_KINDS = ("signal", "sentiment", "regime", "risk", "order", "fill", "close")


def _json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def log(
    db_path: Path,
    kind: str,
    *,
    payload: dict[str, Any],
    symbol: str | None = None,
    ts: datetime | None = None,
) -> None:
    if kind not in _KINDS:
        raise ValueError(f"Unknown decision kind: {kind!r}; must be one of {_KINDS}")
    when = (ts or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO decisions_log(ts, kind, symbol, payload) VALUES (?, ?, ?, ?)",
            (when, kind, symbol, json.dumps(payload, default=_json_default)),
        )
        conn.commit()


def list_recent(
    db_path: Path,
    *,
    limit: int = 200,
    kind: str | None = None,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM decisions_log WHERE 1=1"
    args: list[Any] = []
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    if symbol:
        sql += " AND symbol = ?"
        args.append(symbol)
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]
