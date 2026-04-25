"""Technical indicators. Pure numerical functions over lists[Bar].

All indicators take `list[Bar]` in chronological order and return a scalar
or a numpy array. No pandas dependency inside indicators (keeps them fast
and cheap to reason about). ATR/VWAP/RSI are the only ones we need in
Phase 1 signals; add more as new signals land.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Sequence

import numpy as np

from sentinel.core.types import Bar


def vwap(bars: Sequence[Bar]) -> float:
    if not bars:
        return float("nan")
    typical = np.array([float(b.high + b.low + b.close) / 3 for b in bars])
    vols = np.array([b.volume for b in bars], dtype=float)
    tot = vols.sum()
    if tot == 0:
        return float("nan")
    return float((typical * vols).sum() / tot)


def rsi(bars: Sequence[Bar], window: int = 14) -> float:
    if len(bars) < window + 1:
        return float("nan")
    closes = np.array([float(b.close) for b in bars[-(window + 1):]])
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains.mean()
    avg_loss = losses.mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def atr(bars: Sequence[Bar], window: int = 14) -> float:
    if len(bars) < window + 1:
        return float("nan")
    trs: list[float] = []
    for i in range(1, len(bars)):
        h = float(bars[i].high)
        l = float(bars[i].low)
        pc = float(bars[i - 1].close)
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return float(np.mean(trs[-window:]))


def rolling_vwap_std(bars: Sequence[Bar], window: int) -> tuple[float, float]:
    """Return (vwap, std) over the last `window` bars. std is typical-price std."""
    if len(bars) < window:
        return (float("nan"), float("nan"))
    tail = bars[-window:]
    typical = np.array([float(b.high + b.low + b.close) / 3 for b in tail])
    vols = np.array([b.volume for b in tail], dtype=float)
    tot = vols.sum()
    if tot == 0:
        return (float("nan"), float("nan"))
    v = float((typical * vols).sum() / tot)
    s = float(typical.std())
    return (v, s)


def average_daily_volume(bars: Sequence[Bar], days: int = 20) -> float:
    """Compute ADV from daily bars."""
    if not bars:
        return 0.0
    tail = bars[-days:]
    return float(np.mean([b.volume for b in tail]))


def recent_close(bars: Sequence[Bar]) -> Decimal:
    return bars[-1].close
