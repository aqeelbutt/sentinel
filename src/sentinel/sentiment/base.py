"""NLPBackend Protocol. Any backend satisfying this is swappable.

Contract:
  * `name` — versioned string stored alongside scores in sentiment_cache.
  * `score(text)` returns [-1, 1]: -1 strongly negative, +1 strongly positive.
  * `score_batch(texts)` is the hot path; implementations should batch on GPU
    if available. Callers may pass 1..max_batch items.
"""
from __future__ import annotations

from typing import Protocol


class NLPBackend(Protocol):
    name: str
    max_batch: int

    def score(self, text: str) -> float: ...
    def score_batch(self, texts: list[str]) -> list[float]: ...
