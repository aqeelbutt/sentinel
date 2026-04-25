"""WeightedSentiment recency-decay function."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sentinel.config.schema import SentimentSection
from sentinel.core.clock import FixedClock
from sentinel.sentiment.weighted import WeightedSentiment


def _ws(tmp_path: Path) -> WeightedSentiment:
    cfg = SentimentSection()
    backend = MagicMock()
    backend.name = "test-backend-v1"
    backend.max_batch = 8
    return WeightedSentiment(tmp_path / "x.db", backend, FixedClock.__new__(FixedClock), cfg)


def test_decay_full_under_one_hour(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    assert ws.recency_weight(timedelta(minutes=30)) == pytest.approx(1.0)
    assert ws.recency_weight(timedelta(minutes=59)) == pytest.approx(1.0)


def test_decay_half_at_four_hours(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    assert ws.recency_weight(timedelta(hours=4)) == pytest.approx(0.5, abs=0.01)


def test_decay_zero_at_24_hours(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    assert ws.recency_weight(timedelta(hours=24)) == pytest.approx(0.0)
    assert ws.recency_weight(timedelta(hours=48)) == pytest.approx(0.0)


def test_decay_linear_between_breakpoints(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    # halfway between 1h and 4h → halfway between 1.0 and 0.5 → 0.75
    assert ws.recency_weight(timedelta(hours=2.5)) == pytest.approx(0.75, abs=0.01)
    # halfway between 4h and 24h → halfway between 0.5 and 0.0 → 0.25
    assert ws.recency_weight(timedelta(hours=14)) == pytest.approx(0.25, abs=0.01)
