"""ai_recommendations repo — persists Claude analyst output for audit + dashboard."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sentinel.intelligence.types import AIRecommendation
from sentinel.storage.db import connect


def save(db_path: Path, rec: AIRecommendation) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO ai_recommendations
              (id, symbol, thesis, catalysts_cited, conviction, risk_factors,
               time_horizon, inputs_used, inputs_hash, model, cost_usd,
               raw_response, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec.id, rec.symbol, rec.thesis,
                json.dumps(list(rec.catalysts_cited)),
                rec.conviction,
                json.dumps(list(rec.risk_factors)),
                rec.time_horizon,
                json.dumps(list(rec.inputs_used)),
                rec.inputs_hash, rec.model, rec.cost_usd,
                rec.raw_response,
                rec.created_at.astimezone(timezone.utc).isoformat(),
            ),
        )
        conn.commit()


def list_recent(db_path: Path, limit: int = 20) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM ai_recommendations ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_to_dict(r) for r in rows]


def list_by_inputs_hash(db_path: Path, inputs_hash: str, max_age_minutes: int = 5) -> list[AIRecommendation]:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM ai_recommendations WHERE inputs_hash = ? AND created_at >= ? "
            "ORDER BY created_at DESC",
            (inputs_hash, cutoff),
        ).fetchall()
    out = []
    for r in rows:
        d = _to_dict(r)
        out.append(AIRecommendation(
            id=d["id"], symbol=d["symbol"], thesis=d["thesis"],
            catalysts_cited=tuple(d["catalysts_cited"]),
            conviction=d["conviction"],
            risk_factors=tuple(d["risk_factors"]),
            time_horizon=d["time_horizon"],
            inputs_used=tuple(d["inputs_used"]),
            inputs_hash=d["inputs_hash"], model=d["model"],
            cost_usd=d["cost_usd"], raw_response=d["raw_response"],
            created_at=datetime.fromisoformat(d["created_at"]),
        ))
    return out


def mark_acted_on(db_path: Path, rec_id: str, position_id: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE ai_recommendations SET acted_on = 1, related_position_id = ? WHERE id = ?",
            (position_id, rec_id),
        )
        conn.commit()


def _to_dict(row) -> dict[str, Any]:
    d = dict(row)
    for k in ("catalysts_cited", "risk_factors", "inputs_used"):
        try:
            d[k] = json.loads(d[k]) if d[k] else []
        except (TypeError, json.JSONDecodeError):
            d[k] = []
    return d
