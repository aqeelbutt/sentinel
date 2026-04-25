"""Backtests panel — every persisted backtest run as a row.

Lets you compare strategy variants at a glance. Sort by Sharpe to see the
current best. Click a run to see its trade-level breakdown.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import streamlit as st

from sentinel.dashboard.format import fmt_ts, money, pct
from sentinel.dashboard.state import get_config
from sentinel.storage.db import connect


def render() -> None:
    cfg = get_config()
    st.title("Backtests")
    st.caption(
        "Every backtest run, ranked. Run more with `sentinel backtest --start … --end …`. "
        "Persisted trades from these runs power the Recommendations forecast section."
    )

    runs = _list_runs(cfg.storage.db_path)
    if not runs:
        st.info(
            "No backtest runs yet. Try:\n\n"
            "```\nsentinel backtest --start 2024-04-01 --end 2026-04-01 --timeframe 1d\n```"
        )
        return

    df = pd.DataFrame(runs)
    df_view = df[[
        "run_id", "n_trades", "wins", "win_rate", "avg_win_pct", "avg_loss_pct",
        "total_pnl", "profit_factor", "first_trade", "last_trade",
        "backtest_start", "backtest_end", "timeframe",
    ]].rename(columns={
        "n_trades": "Trades", "wins": "Wins", "win_rate": "Win %",
        "avg_win_pct": "Avg win", "avg_loss_pct": "Avg loss",
        "total_pnl": "Total $", "profit_factor": "PF",
        "first_trade": "First", "last_trade": "Last",
        "backtest_start": "Start", "backtest_end": "End", "timeframe": "TF",
    })
    df_view["Win %"] = df_view["Win %"].apply(lambda v: f"{v:.0%}")
    df_view["Avg win"] = df_view["Avg win"].apply(lambda v: f"{v:+.2%}" if v is not None else "—")
    df_view["Avg loss"] = df_view["Avg loss"].apply(lambda v: f"{v:+.2%}" if v is not None else "—")
    df_view["PF"] = df_view["PF"].apply(lambda v: f"{v:.2f}" if v is not None else "—")
    df_view["Total $"] = df_view["Total $"].apply(money)
    st.dataframe(df_view, hide_index=True, width="stretch")

    st.divider()
    st.subheader("Drill into a run")
    run_id = st.selectbox(
        "Run id",
        options=[r["run_id"] for r in runs],
        format_func=lambda i: f"{i}  ({next(r for r in runs if r['run_id']==i)['n_trades']} trades, "
                              f"PF {next(r for r in runs if r['run_id']==i)['profit_factor']})",
    )
    trades = _trades_for_run(cfg.storage.db_path, run_id)
    if not trades:
        st.info("No trades in this run.")
        return

    # Per-signal-mix performance
    st.markdown("**Performance by signal mix** (the historical edge of each combination)")
    by_mix = _aggregate_by_signals(trades)
    st.dataframe(by_mix, hide_index=True, width="stretch")

    # Per-symbol performance
    st.markdown("**Performance by symbol**")
    by_sym = _aggregate_by_symbol(trades)
    st.dataframe(by_sym, hide_index=True, width="stretch")

    # Trade-level table
    st.markdown(f"**All {len(trades)} trades**")
    st.dataframe(pd.DataFrame(trades)[
        ["entry_ts", "exit_ts", "symbol", "qty", "entry_price", "exit_price",
         "realized_pnl", "realized_pnl_pct", "close_reason", "signals"]
    ], hide_index=True, width="stretch")


# ---------- helpers ----------

def _list_runs(db_path) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT run_id, timeframe, backtest_start, backtest_end,
                   COUNT(*) AS n_trades,
                   SUM(CASE WHEN realized_pnl_pct > 0 THEN 1 ELSE 0 END) AS wins,
                   AVG(CASE WHEN realized_pnl_pct > 0 THEN realized_pnl_pct END) AS avg_win_pct,
                   AVG(CASE WHEN realized_pnl_pct < 0 THEN realized_pnl_pct END) AS avg_loss_pct,
                   SUM(CAST(realized_pnl AS REAL)) AS total_pnl,
                   MIN(entry_ts) AS first_trade,
                   MAX(exit_ts)  AS last_trade
            FROM backtest_trades
            GROUP BY run_id
            ORDER BY MAX(exit_ts) DESC
            """
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        n = d["n_trades"] or 0
        d["win_rate"] = (d["wins"] or 0) / n if n else 0.0
        # Profit factor needs separate query
        pf_row = conn.execute(
            "SELECT SUM(CASE WHEN CAST(realized_pnl AS REAL) > 0 THEN CAST(realized_pnl AS REAL) ELSE 0 END) AS gw, "
            "       SUM(CASE WHEN CAST(realized_pnl AS REAL) < 0 THEN -CAST(realized_pnl AS REAL) ELSE 0 END) AS gl "
            "FROM backtest_trades WHERE run_id = ?",
            (d["run_id"],),
        ).fetchone() if False else None
        d["profit_factor"] = None
        out.append(d)
    # second pass for profit factor (separate connection so generator above closes)
    with connect(db_path) as conn:
        for d in out:
            row = conn.execute(
                "SELECT SUM(CASE WHEN CAST(realized_pnl AS REAL) > 0 THEN CAST(realized_pnl AS REAL) ELSE 0 END) AS gw, "
                "       SUM(CASE WHEN CAST(realized_pnl AS REAL) < 0 THEN -CAST(realized_pnl AS REAL) ELSE 0 END) AS gl "
                "FROM backtest_trades WHERE run_id = ?",
                (d["run_id"],),
            ).fetchone()
            gw = row["gw"] or 0.0
            gl = row["gl"] or 0.0
            d["profit_factor"] = (gw / gl) if gl > 0 else None
    return out


def _trades_for_run(db_path, run_id: str) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT entry_ts, exit_ts, symbol, qty, entry_price, exit_price, "
            "realized_pnl, realized_pnl_pct, close_reason, signals "
            "FROM backtest_trades WHERE run_id = ? ORDER BY entry_ts",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _aggregate_by_signals(trades: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(trades)
    if df.empty:
        return df
    grp = df.groupby("signals").agg(
        n=("realized_pnl_pct", "size"),
        win_rate=("realized_pnl_pct", lambda s: (s > 0).mean()),
        avg_win=("realized_pnl_pct", lambda s: s[s > 0].mean()),
        avg_loss=("realized_pnl_pct", lambda s: s[s < 0].mean()),
        expectancy=("realized_pnl_pct", "mean"),
        total_pnl=("realized_pnl", lambda s: pd.to_numeric(s, errors="coerce").sum()),
    ).reset_index().sort_values("expectancy", ascending=False)
    grp["win_rate"] = (grp["win_rate"] * 100).round(1).astype(str) + "%"
    for c in ("avg_win", "avg_loss", "expectancy"):
        grp[c] = grp[c].apply(lambda v: f"{v:+.2%}" if pd.notna(v) else "—")
    grp["total_pnl"] = grp["total_pnl"].apply(money)
    return grp


def _aggregate_by_symbol(trades: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(trades)
    if df.empty:
        return df
    grp = df.groupby("symbol").agg(
        n=("realized_pnl_pct", "size"),
        win_rate=("realized_pnl_pct", lambda s: (s > 0).mean()),
        expectancy=("realized_pnl_pct", "mean"),
        total_pnl=("realized_pnl", lambda s: pd.to_numeric(s, errors="coerce").sum()),
    ).reset_index().sort_values("total_pnl", ascending=False)
    grp["win_rate"] = (grp["win_rate"] * 100).round(1).astype(str) + "%"
    grp["expectancy"] = grp["expectancy"].apply(lambda v: f"{v:+.2%}" if pd.notna(v) else "—")
    grp["total_pnl"] = grp["total_pnl"].apply(money)
    return grp
