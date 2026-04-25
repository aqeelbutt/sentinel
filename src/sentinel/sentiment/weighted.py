"""Weighted sentiment aggregator.

Combines article scores from the NLP backend with tiered source weights and
piecewise-linear recency decay to produce a SymbolSentiment. Caches scored
articles in SQLite so re-scoring is cheap.

Qualification gate (from CLAUDE.md): score > 0.7 AND ≥ 3 canonically-
independent sources AND newest article < 4h old.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sentinel.config.schema import SentimentSection
from sentinel.core.clock import Clock
from sentinel.core.types import SymbolSentiment
from sentinel.data.news.aggregator import Article
from sentinel.sentiment.base import NLPBackend
from sentinel.sentiment.tiers import canonical_origin, tier_weight
from sentinel.storage.repos import sentiment_cache

log = logging.getLogger(__name__)


class WeightedSentiment:
    def __init__(
        self,
        db_path: Path,
        backend: NLPBackend,
        clock: Clock,
        cfg: SentimentSection,
    ) -> None:
        self.db_path = db_path
        self.backend = backend
        self.clock = clock
        self.cfg = cfg

    def ingest(self, articles: list[Article]) -> int:
        """Score any articles not already in the cache. Returns count newly scored."""
        if not articles:
            return 0
        to_score: list[Article] = []
        for a in articles:
            existing = sentiment_cache.fetch_for_symbol(
                self.db_path,
                symbol=a.symbols[0] if a.symbols else "",
                model_name=self.backend.name,
                after=a.published_at - timedelta(seconds=1),
                before=a.published_at + timedelta(seconds=1),
            )
            if not any(e["article_id"] == a.id for e in existing):
                to_score.append(a)
        if not to_score:
            return 0
        batch_size = self.backend.max_batch
        for i in range(0, len(to_score), batch_size):
            chunk = to_score[i : i + batch_size]
            texts = [f"{a.title}. {a.body}" for a in chunk]
            try:
                scores = self.backend.score_batch(texts)
            except Exception as e:  # noqa: BLE001
                log.exception("nlp backend failed", extra={"error": str(e)})
                scores = [0.0] * len(chunk)
            for a, s in zip(chunk, scores):
                for sym in a.symbols:
                    sentiment_cache.upsert(
                        self.db_path,
                        article_id=a.id,
                        model_name=self.backend.name,
                        raw_score=float(s),
                        source=a.source,
                        published_at=a.published_at,
                        title=a.title,
                        symbols=list(a.symbols),
                    )
        return len(to_score)

    def score_symbol(self, symbol: str, at: datetime | None = None) -> SymbolSentiment:
        at = (at or self.clock.now()).astimezone(timezone.utc)
        window_hours = max(self.cfg.decay_breakpoints_hours)
        lower = at - timedelta(hours=window_hours)
        rows = sentiment_cache.fetch_for_symbol(
            self.db_path,
            symbol=symbol.upper(),
            model_name=self.backend.name,
            after=lower,
            before=at,
        )
        if not rows:
            return _empty(symbol, at)

        weighted_sum = 0.0
        total_weight = 0.0
        canonical_sources: set[str] = set()
        contributing: list[str] = []
        newest: datetime | None = None

        for r in rows:
            published = datetime.fromisoformat(r["published_at"])
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            age = at - published
            r_weight = self.recency_weight(age)
            if r_weight <= 0:
                continue
            s_weight = tier_weight(r["source"], self.cfg.tier_weights, self.cfg.source_tiers)
            if s_weight <= 0:
                continue
            w = r_weight * s_weight
            weighted_sum += r["raw_score"] * w
            total_weight += w
            canonical_sources.add(canonical_origin(r["source"]))
            contributing.append(r["article_id"])
            if newest is None or published > newest:
                newest = published

        if total_weight == 0 or newest is None:
            return _empty(symbol, at)

        score = weighted_sum / total_weight
        newest_age = (at - newest).total_seconds() / 3600
        qualifies = (
            score > self.cfg.qualify_threshold
            and len(canonical_sources) >= self.cfg.qualify_min_independent_sources
            and newest_age < self.cfg.qualify_max_newest_age_hours
        )
        confidence = min(1.0, len(canonical_sources) / 5 * max(0.0, 1.0 - newest_age / 24))
        return SymbolSentiment(
            symbol=symbol.upper(),
            score=round(score, 4),
            confidence=round(confidence, 4),
            article_count=len(contributing),
            independent_sources=len(canonical_sources),
            newest_age_hours=round(newest_age, 2),
            qualifies=qualifies,
            computed_at=at,
            contributing_article_ids=tuple(contributing[:50]),
        )

    def recency_weight(self, age: timedelta) -> float:
        """Piecewise linear. 1.0 < 1h; 0.5 at 4h; 0.0 at 24h."""
        h = age.total_seconds() / 3600
        b1, b2, b3 = self.cfg.decay_breakpoints_hours
        if h <= b1:
            return 1.0
        if h <= b2:
            # linear 1.0 → 0.5
            return 1.0 - 0.5 * (h - b1) / (b2 - b1)
        if h <= b3:
            # linear 0.5 → 0.0
            return 0.5 * (1.0 - (h - b2) / (b3 - b2))
        return 0.0


def _empty(symbol: str, at: datetime) -> SymbolSentiment:
    return SymbolSentiment(
        symbol=symbol.upper(),
        score=0.0,
        confidence=0.0,
        article_count=0,
        independent_sources=0,
        newest_age_hours=float("inf"),
        qualifies=False,
        computed_at=at,
    )


def build_backend(cfg: SentimentSection) -> NLPBackend:
    if cfg.backend == "finbert":
        from sentinel.sentiment.finbert import FinBERTBackend
        return FinBERTBackend()
    from sentinel.sentiment.stub import StubNLPBackend
    return StubNLPBackend()
