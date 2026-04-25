"""Neutral stub backend. Returns 0.0 for every input.

Default backend because:
  1. Lets the system boot without downloading ~500 MB of FinBERT weights.
  2. Makes integration tests deterministic.
  3. Honestly represents "no NLP signal" — the sentiment leg of the Triple-Check
     will not qualify any symbol until a real backend is configured, which
     means recommendations never fire on sentiment alone. That's the correct
     behavior before FinBERT (or a real provider) is wired up.
"""
from __future__ import annotations


class StubNLPBackend:
    name = "stub-neutral-v1"
    max_batch = 64

    def score(self, text: str) -> float:
        return 0.0

    def score_batch(self, texts: list[str]) -> list[float]:
        return [0.0] * len(texts)
