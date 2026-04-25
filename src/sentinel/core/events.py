"""Market event union flowing through the StrategyPipeline.

Events are the only thing the pipeline consumes. A live feed yields these
over a websocket; the backtest replay yields them from parquet/SQLite.
The pipeline itself cannot tell which.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Union

from sentinel.core.types import Bar, Quote


@dataclass(frozen=True)
class BarEvent:
    bar: Bar


@dataclass(frozen=True)
class QuoteEvent:
    quote: Quote


@dataclass(frozen=True)
class NewsEvent:
    article_id: str
    source: str
    symbols: tuple[str, ...]
    published_at: datetime
    title: str


@dataclass(frozen=True)
class HeartbeatEvent:
    ts: datetime


MarketEvent = Union[BarEvent, QuoteEvent, NewsEvent, HeartbeatEvent]
