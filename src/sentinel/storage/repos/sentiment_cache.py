"""Sentiment scoring cache — keyed by (article_id, model_name) so swapping
NLP models doesn't invalidate prior runs we may want to compare against.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sentinel.storage.db import connect


def upsert(
    db_path: Path,
    *,
    article_id: str,
    model_name: str,
    raw_score: float,
    source: str,
    published_at: datetime,
    title: str | None,
    symbols: list[str],
) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sentiment_cache
                (article_id, model_name, raw_score, scored_at,
                 source, published_at, title, symbols)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id, model_name) DO UPDATE SET
                raw_score = excluded.raw_score,
                scored_at = excluded.scored_at
            """,
            (
                article_id,
                model_name,
                raw_score,
                datetime.now(timezone.utc).isoformat(),
                source,
                published_at.astimezone(timezone.utc).isoformat(),
                title,
                json.dumps(symbols),
            ),
        )
        conn.commit()


def fetch_for_symbol(
    db_path: Path,
    *,
    symbol: str,
    model_name: str,
    after: datetime,
    before: datetime | None = None,
) -> list[dict[str, Any]]:
    """Articles tagged with `symbol` published in (after, before]. before defaults to now."""
    upper = (before or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    lower = after.astimezone(timezone.utc).isoformat()
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM sentiment_cache
             WHERE model_name = ?
               AND published_at > ?
               AND published_at <= ?
               AND symbols LIKE ?
            """,
            (model_name, lower, upper, f'%"{symbol}"%'),
        ).fetchall()
    return [dict(r) for r in rows]
