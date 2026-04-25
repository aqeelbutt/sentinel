"""Risk-adjusted performance metrics.

We compare every metric to SPY buy-and-hold over the same window. If the
strategy doesn't beat SPY on a Sharpe basis after enough data, that's the
honest evaluation the user asked for.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

import numpy as np

from sentinel.data.market import bar_store
from sentinel.storage.repos import equity_snapshots, positions

Window = Literal["1w", "2w", "3w", "1mo", "3mo", "6mo", "1y", "inception"]


_WINDOW_DAYS = {
    "1w": 7,
    "2w": 14,
    "3w": 21,
    "1mo": 30,
    "3mo": 90,
    "6mo": 180,
    "1y": 365,
}


@dataclass(frozen=True)
class WindowMetrics:
    window: Window
    start_date: date
    end_date: date
    days: int

    portfolio_total_return: float
    portfolio_cagr: float
    portfolio_sharpe: float
    portfolio_sortino: float
    portfolio_max_drawdown: float
    portfolio_win_rate: float | None
    trades_in_window: int

    spy_total_return: float
    spy_sharpe: float
    spy_max_drawdown: float

    alpha_vs_spy: float            # excess return over SPY
    information_ratio: float | None  # alpha / tracking error
    beta_vs_spy: float | None

    verdict: str                    # "BEATS_SPY" | "TIES_SPY" | "LOSES_TO_SPY" | "INCONCLUSIVE"


def compute_window_metrics(db_path: Path, window: Window, today: date | None = None) -> WindowMetrics | None:
    today = today or datetime.now(timezone.utc).date()
    if window == "inception":
        snaps_all = equity_snapshots.list_all(db_path)
        if not snaps_all:
            return None
        start = date.fromisoformat(snaps_all[0]["snapshot_date"])
    else:
        days = _WINDOW_DAYS[window]
        start = today - timedelta(days=days)

    snaps = equity_snapshots.list_since(db_path, start)
    if len(snaps) < 2:
        return None

    equities = np.array([float(Decimal(s["equity"])) for s in snaps])
    returns = np.diff(equities) / equities[:-1]
    portfolio_total = (equities[-1] - equities[0]) / equities[0] if equities[0] > 0 else 0.0
    days_actual = max(1, (today - start).days)
    cagr = (1 + portfolio_total) ** (365 / days_actual) - 1 if portfolio_total > -1 else -1.0
    sharpe = _sharpe(returns)
    sortino = _sortino(returns)
    mdd = _max_drawdown(equities)

    # SPY buy-and-hold over the same window
    spy_bars = bar_store.get_bars(db_path, "SPY", "1d", days=max(_WINDOW_DAYS.get(window, 365) + 30, 60))
    spy_in_window = [
        b for b in spy_bars
        if start <= b.ts.astimezone(timezone.utc).date() <= today
    ]
    if len(spy_in_window) >= 2:
        spy_closes = np.array([float(b.close) for b in spy_in_window])
        spy_returns = np.diff(spy_closes) / spy_closes[:-1]
        spy_total = (spy_closes[-1] - spy_closes[0]) / spy_closes[0]
        spy_sharpe = _sharpe(spy_returns)
        spy_mdd = _max_drawdown(spy_closes)
    else:
        spy_total = 0.0
        spy_sharpe = 0.0
        spy_mdd = 0.0
        spy_returns = np.array([])

    alpha = portfolio_total - spy_total
    info_ratio: float | None = None
    beta: float | None = None
    if spy_returns.size >= 5 and returns.size >= 5:
        n = min(returns.size, spy_returns.size)
        active = returns[-n:] - spy_returns[-n:]
        te = active.std()
        info_ratio = float(active.mean() / te * np.sqrt(252)) if te > 0 else None
        cov = np.cov(returns[-n:], spy_returns[-n:])[0, 1]
        var_spy = spy_returns[-n:].var()
        beta = float(cov / var_spy) if var_spy > 0 else None

    closed = positions.list_closed(db_path, since=datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc))
    trades_in_window = len(closed)
    wins = sum(1 for c in closed if c["realized_pnl"] is not None and Decimal(c["realized_pnl"]) > 0)
    win_rate = (wins / trades_in_window) if trades_in_window > 0 else None

    verdict = _verdict(sharpe, spy_sharpe, trades_in_window)
    return WindowMetrics(
        window=window,
        start_date=start,
        end_date=today,
        days=days_actual,
        portfolio_total_return=portfolio_total,
        portfolio_cagr=cagr,
        portfolio_sharpe=sharpe,
        portfolio_sortino=sortino,
        portfolio_max_drawdown=mdd,
        portfolio_win_rate=win_rate,
        trades_in_window=trades_in_window,
        spy_total_return=spy_total,
        spy_sharpe=spy_sharpe,
        spy_max_drawdown=spy_mdd,
        alpha_vs_spy=alpha,
        information_ratio=info_ratio,
        beta_vs_spy=beta,
        verdict=verdict,
    )


def _sharpe(returns: np.ndarray, rf: float = 0.0) -> float:
    if returns.size < 2 or returns.std() == 0:
        return 0.0
    return float((returns.mean() - rf) / returns.std() * np.sqrt(252))


def _sortino(returns: np.ndarray, rf: float = 0.0) -> float:
    if returns.size < 2:
        return 0.0
    downside = returns[returns < rf]
    if downside.size == 0 or downside.std() == 0:
        return 0.0
    return float((returns.mean() - rf) / downside.std() * np.sqrt(252))


def _max_drawdown(equities: np.ndarray) -> float:
    if equities.size < 2:
        return 0.0
    running_max = np.maximum.accumulate(equities)
    drawdowns = (equities - running_max) / running_max
    return float(drawdowns.min())


def _verdict(port_sharpe: float, spy_sharpe: float, trades: int) -> str:
    if trades < 10:
        return "INCONCLUSIVE"
    margin = 0.2
    if port_sharpe > spy_sharpe + margin:
        return "BEATS_SPY"
    if port_sharpe < spy_sharpe - margin:
        return "LOSES_TO_SPY"
    return "TIES_SPY"


def snapshot_today(db_path: Path, *, starting_equity: Decimal) -> None:
    """Persist today's equity snapshot. Called by scanner and by `sentinel scan`."""
    from sentinel.risk.manager import compute_equity_state
    from sentinel.core.clock import MarketClock
    clock = MarketClock()
    state = compute_equity_state(db_path, starting_equity, clock)
    open_pos = positions.list_open(db_path)
    spy_bars = bar_store.get_bars(db_path, "SPY", "1d", days=5)
    spy_close = spy_bars[-1].close if spy_bars else None
    equity_snapshots.upsert(
        db_path,
        snapshot_date=clock.now().date(),
        equity=state.equity,
        cash=state.cash,
        deployed=state.deployed,
        open_positions=len(open_pos),
        spy_close=spy_close,
    )
