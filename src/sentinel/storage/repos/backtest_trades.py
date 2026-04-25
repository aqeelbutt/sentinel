"""Persisted backtest trade history. Powers the Recommendation page's
'forecast' section — for a candidate rec with signal mix S, we look up
historical trades with the same mix and report observed win rate / avg P&L.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sentinel.storage.db import connect


def insert_run(
    db_path: Path,
    *,
    run_id: str,
    config_hash: str,
    timeframe: str,
    backtest_start: date,
    backtest_end: date,
    trades: list[Any],   # list[SimTrade]; typed loose to avoid import cycle
) -> int:
    if not trades:
        return 0
    rows = []
    for t in trades:
        rows.append((
            run_id, config_hash,
            t.symbol, t.qty, str(t.entry_price), str(t.exit_price),
            t.entry_ts.isoformat(), t.exit_ts.isoformat(),
            str(t.realized_pnl), float(t.realized_pnl_pct),
            t.close_reason, ",".join(sorted(t.signals)),
            timeframe, backtest_start.isoformat(), backtest_end.isoformat(),
        ))
    with connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO backtest_trades
              (run_id, config_hash, symbol, qty, entry_price, exit_price,
               entry_ts, exit_ts, realized_pnl, realized_pnl_pct,
               close_reason, signals, timeframe, backtest_start, backtest_end)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def stats_for_signal_mix(db_path: Path, signals: list[str]) -> dict[str, Any] | None:
    """Aggregate stats for trades that had this exact signal mix (sorted).

    Returns None if there are < 5 historical trades to sample from.
    """
    key = ",".join(sorted(signals))
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) as n,
                   SUM(CASE WHEN realized_pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                   AVG(CASE WHEN realized_pnl_pct > 0 THEN realized_pnl_pct END) as avg_win,
                   AVG(CASE WHEN realized_pnl_pct < 0 THEN realized_pnl_pct END) as avg_loss,
                   AVG(realized_pnl_pct) as avg_pct,
                   MIN(realized_pnl_pct) as worst,
                   MAX(realized_pnl_pct) as best
            FROM backtest_trades
            WHERE signals = ?
            """,
            (key,),
        ).fetchone()
    if not row or row["n"] < 5:
        return None
    n = row["n"]
    return {
        "n": n,
        "win_rate": (row["wins"] or 0) / n,
        "avg_win_pct": row["avg_win"] or 0.0,
        "avg_loss_pct": row["avg_loss"] or 0.0,
        "expectancy_pct": row["avg_pct"] or 0.0,
        "best_pct": row["best"] or 0.0,
        "worst_pct": row["worst"] or 0.0,
        "signal_mix": key,
    }


def latest_run(db_path: Path) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT run_id, MAX(entry_ts) as latest_entry, COUNT(*) as n_trades, "
            "MIN(backtest_start) as start, MAX(backtest_end) as end, timeframe "
            "FROM backtest_trades GROUP BY run_id ORDER BY latest_entry DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None
