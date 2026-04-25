"""30-day correlation check. Rejects a new position if any existing open
position has > max_correlation Pearson correlation on daily-close returns.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np

from sentinel.data.market import bar_store


def max_correlation_with(
    db_path: Path,
    *,
    candidate: str,
    existing: Iterable[str],
    lookback_days: int,
) -> tuple[str | None, float]:
    """Return (symbol_with_highest_corr, value). 0.0 if no history overlap."""
    existing = [s for s in existing if s.upper() != candidate.upper()]
    if not existing:
        return (None, 0.0)

    cand_returns = _daily_returns(db_path, candidate, lookback_days)
    if cand_returns.size == 0:
        return (None, 0.0)

    best_sym = None
    best_corr = -2.0
    for sym in existing:
        r = _daily_returns(db_path, sym, lookback_days)
        n = min(r.size, cand_returns.size)
        if n < 10:
            continue
        corr = float(np.corrcoef(cand_returns[-n:], r[-n:])[0, 1])
        if np.isnan(corr):
            continue
        if abs(corr) > abs(best_corr):
            best_corr = corr
            best_sym = sym
    if best_sym is None:
        return (None, 0.0)
    return (best_sym, best_corr)


def _daily_returns(db_path: Path, symbol: str, lookback_days: int) -> np.ndarray:
    bars = bar_store.get_bars(db_path, symbol, "1d", days=lookback_days + 5)
    closes = np.array([float(b.close) for b in bars[-lookback_days - 1:]])
    if closes.size < 2:
        return np.array([])
    return np.diff(closes) / closes[:-1]
