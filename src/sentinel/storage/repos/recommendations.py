"""Recommendations persistence."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sentinel.core.types import Recommendation, RecommendationAction, SignalName
from sentinel.storage.db import connect


def _serialize_payload(rec: Recommendation) -> str:
    """JSON snapshot for audit. Must NOT be parsed back; use the typed columns."""
    def _default(o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, (SignalName, RecommendationAction)):
            return o.value
        if hasattr(o, "__dataclass_fields__"):
            return asdict(o)
        return str(o)

    payload = {
        "symbol": rec.symbol,
        "action": rec.action.value,
        "score": rec.score,
        "signals_fired": [s.value for s in rec.signals_fired],
        "sentiment": asdict(rec.sentiment) if rec.sentiment else None,
        "regime": asdict(rec.regime) if rec.regime else None,
        "rationale": rec.rationale,
    }
    return json.dumps(payload, default=_default)


def save(db_path: Path, rec: Recommendation) -> str:
    rec_id = str(uuid.uuid4())
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO recommendations
                (id, symbol, action, score, entry_price_ref, suggested_stop,
                 suggested_take_profit, suggested_qty, signals_fired,
                 sentiment_score, regime_hostile, rationale,
                 created_at, expires_at, full_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec_id,
                rec.symbol,
                rec.action.value,
                rec.score,
                str(rec.entry_price_ref),
                str(rec.suggested_stop),
                str(rec.suggested_take_profit) if rec.suggested_take_profit else None,
                rec.suggested_qty,
                json.dumps([s.value for s in rec.signals_fired]),
                rec.sentiment.score if rec.sentiment else None,
                int(rec.regime.hostile) if rec.regime else None,
                rec.rationale,
                rec.created_at.astimezone(timezone.utc).isoformat(),
                rec.expires_at.astimezone(timezone.utc).isoformat(),
                _serialize_payload(rec),
            ),
        )
        conn.commit()
    return rec_id


def list_recent(db_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM recommendations ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_active(db_path: Path, now_utc_iso: str) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM recommendations WHERE expires_at > ? "
            "AND action = 'buy' ORDER BY score DESC, created_at DESC",
            (now_utc_iso,),
        ).fetchall()
    return [dict(r) for r in rows]


def get(db_path: Path, rec_id: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM recommendations WHERE id = ?", (rec_id,)).fetchone()
    return dict(row) if row else None
