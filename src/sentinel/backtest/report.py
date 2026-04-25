"""Backtest result types + metric computation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np


@dataclass
class SimTrade:
    symbol: str
    qty: int
    entry_price: Decimal
    exit_price: Decimal
    entry_ts: datetime
    exit_ts: datetime
    realized_pnl: Decimal
    realized_pnl_pct: float
    close_reason: str          # profit_take | hard_stop | end_of_backtest
    signals: tuple[str, ...]


@dataclass
class BacktestReport:
    # identity
    start: date
    end: date
    symbols: tuple[str, ...]
    timeframe: str
    initial_equity: Decimal
    final_equity: Decimal

    # portfolio metrics
    total_return_pct: float
    cagr_pct: float
    sharpe: float
    sortino: float
    max_drawdown_pct: float
    n_trades: int
    win_rate: float
    avg_win_pct: float | None
    avg_loss_pct: float | None
    profit_factor: float | None
    avg_holding_minutes: float

    # benchmark
    spy_total_return_pct: float
    spy_sharpe: float
    spy_max_drawdown_pct: float
    alpha_vs_spy_pct: float

    # diagnostics
    closes_by_reason: dict[str, int]
    signals_fired_counts: dict[str, int]
    rejection_counts: dict[str, int]
    bars_evaluated: int

    # raw series (kept lightweight)
    equity_curve: list[tuple[datetime, Decimal]] = field(default_factory=list)
    trades: list[SimTrade] = field(default_factory=list)

    def verdict(self) -> str:
        """BEATS_SPY | TIES_SPY | LOSES_TO_SPY | INCONCLUSIVE — Sharpe-based, ±0.2 margin."""
        if self.n_trades < 10:
            return "INCONCLUSIVE"
        margin = 0.2
        if self.sharpe > self.spy_sharpe + margin:
            return "BEATS_SPY"
        if self.sharpe < self.spy_sharpe - margin:
            return "LOSES_TO_SPY"
        return "TIES_SPY"


def compute_metrics(
    *,
    start: date,
    end: date,
    symbols: tuple[str, ...],
    timeframe: str,
    initial_equity: Decimal,
    equity_curve: list[tuple[datetime, Decimal]],
    trades: list[SimTrade],
    spy_curve: list[tuple[datetime, float]],
    closes_by_reason: dict[str, int],
    signals_fired_counts: dict[str, int],
    rejection_counts: dict[str, int],
    bars_evaluated: int,
) -> BacktestReport:
    final_equity = equity_curve[-1][1] if equity_curve else initial_equity
    total_return = float((final_equity - initial_equity) / initial_equity) if initial_equity > 0 else 0.0

    days = max(1, (end - start).days)
    cagr = (1 + total_return) ** (365 / days) - 1 if total_return > -1 else -1.0

    # Per-day returns for portfolio (resample to date)
    daily = _to_daily_series(equity_curve)
    port_returns = _pct_changes(daily)
    sharpe = _annualized_sharpe(port_returns)
    sortino = _annualized_sortino(port_returns)
    mdd = _max_drawdown(daily)

    # Trade stats
    n = len(trades)
    wins_pct = [t.realized_pnl_pct for t in trades if t.realized_pnl > 0]
    losses_pct = [t.realized_pnl_pct for t in trades if t.realized_pnl < 0]
    win_rate = len(wins_pct) / n if n > 0 else 0.0
    avg_win = float(np.mean(wins_pct)) if wins_pct else None
    avg_loss = float(np.mean(losses_pct)) if losses_pct else None
    gross_win = sum(float(t.realized_pnl) for t in trades if t.realized_pnl > 0)
    gross_loss = abs(sum(float(t.realized_pnl) for t in trades if t.realized_pnl < 0))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None
    holding_minutes = (
        float(np.mean([(t.exit_ts - t.entry_ts).total_seconds() / 60 for t in trades]))
        if trades else 0.0
    )

    # SPY benchmark
    spy_daily = _to_daily_series_floats(spy_curve)
    spy_returns = _pct_changes(spy_daily)
    spy_total = (spy_daily[-1] - spy_daily[0]) / spy_daily[0] if len(spy_daily) >= 2 else 0.0
    spy_sharpe = _annualized_sharpe(spy_returns)
    spy_mdd = _max_drawdown(spy_daily)

    return BacktestReport(
        start=start, end=end, symbols=symbols, timeframe=timeframe,
        initial_equity=initial_equity, final_equity=final_equity,
        total_return_pct=total_return, cagr_pct=cagr,
        sharpe=sharpe, sortino=sortino, max_drawdown_pct=mdd,
        n_trades=n, win_rate=win_rate,
        avg_win_pct=avg_win, avg_loss_pct=avg_loss,
        profit_factor=profit_factor, avg_holding_minutes=holding_minutes,
        spy_total_return_pct=spy_total, spy_sharpe=spy_sharpe,
        spy_max_drawdown_pct=spy_mdd,
        alpha_vs_spy_pct=total_return - spy_total,
        closes_by_reason=closes_by_reason,
        signals_fired_counts=signals_fired_counts,
        rejection_counts=rejection_counts,
        bars_evaluated=bars_evaluated,
        equity_curve=equity_curve, trades=trades,
    )


# ---------- helpers ----------

def _to_daily_series(curve: list[tuple[datetime, Decimal]]) -> list[float]:
    if not curve:
        return []
    by_day: dict[date, float] = {}
    for ts, eq in curve:
        by_day[ts.date()] = float(eq)   # last value of the day wins
    return [by_day[d] for d in sorted(by_day.keys())]


def _to_daily_series_floats(curve: list[tuple[datetime, float]]) -> list[float]:
    if not curve:
        return []
    by_day: dict[date, float] = {}
    for ts, v in curve:
        by_day[ts.date()] = float(v)
    return [by_day[d] for d in sorted(by_day.keys())]


def _pct_changes(series: list[float]) -> np.ndarray:
    if len(series) < 2:
        return np.array([])
    arr = np.array(series, dtype=float)
    return np.diff(arr) / arr[:-1]


def _annualized_sharpe(returns: np.ndarray, rf: float = 0.0) -> float:
    if returns.size < 2 or returns.std() == 0:
        return 0.0
    return float((returns.mean() - rf) / returns.std() * np.sqrt(252))


def _annualized_sortino(returns: np.ndarray, rf: float = 0.0) -> float:
    if returns.size < 2:
        return 0.0
    downside = returns[returns < rf]
    if downside.size == 0 or downside.std() == 0:
        return 0.0
    return float((returns.mean() - rf) / downside.std() * np.sqrt(252))


def _max_drawdown(series: list[float]) -> float:
    if len(series) < 2:
        return 0.0
    arr = np.array(series, dtype=float)
    running_max = np.maximum.accumulate(arr)
    drawdowns = (arr - running_max) / running_max
    return float(drawdowns.min())
