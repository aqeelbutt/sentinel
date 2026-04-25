"""Sentinel CLI. `sentinel --help` for the full surface.

Commands:
  sentinel doctor               run health checks
  sentinel scan                 one-off scan; persists recommendations
  sentinel run --interval 5m    loop: scan every N minutes, respects market hours
  sentinel dashboard            launch Streamlit UI
  sentinel watch list/add/rm    manage the watchlist
  sentinel list                 print current virtual portfolio
  sentinel add SYM --qty N      manual buy at last quote (bypasses recs)
  sentinel close SYM            close a virtual position at market
  sentinel report --window 1mo  performance summary vs SPY for a window
  sentinel sweep                run profit-take/auto-stop sweep on demand
"""
from __future__ import annotations

import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from sentinel.config.schema import Config
from sentinel.core.clock import MarketClock
from sentinel.core.config import load_config
from sentinel.core.identity import resolve_virtual_token
from sentinel.core.types import OrderIntent, Side
from sentinel.execution.virtual_broker import VirtualBroker
from sentinel.ops.logging_setup import configure as configure_logging
from sentinel.risk.manager import RiskManager, compute_equity_state
from sentinel.storage.db import init_db
from sentinel.storage.repos import positions, watchlist

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="Sentinel — Triple-Check equity recommendations + virtual portfolio tracker.",
)

watch_app = typer.Typer(help="Manage the watchlist.")
app.add_typer(watch_app, name="watch")

keychain_app = typer.Typer(help="Manage API keys in macOS Keychain.")
app.add_typer(keychain_app, name="keychain")

catalysts_app = typer.Typer(help="Catalyst calendar (earnings, IPOs, macro).")
app.add_typer(catalysts_app, name="catalysts")

console = Console()


# ---------- bootstrap ----------

def _bootstrap() -> tuple[Config, MarketClock, VirtualBroker, RiskManager]:
    cfg = load_config()
    configure_logging(cfg.logging.level, cfg.logging.json_file, cfg.logging.console)
    init_db(cfg.storage.db_path, wal=cfg.storage.wal)
    if not watchlist.list_all(cfg.storage.db_path):
        watchlist.replace_all(cfg.storage.db_path, cfg.watchlist.symbols)
    clock = MarketClock()
    broker = VirtualBroker(cfg, cfg.storage.db_path, clock, resolve_virtual_token())
    risk = RiskManager(cfg, cfg.storage.db_path, clock)
    return cfg, clock, broker, risk


# ---------- commands ----------

@app.command()
def doctor() -> None:
    """Run pre-flight health checks. Exit 1 on any failure."""
    from sentinel.ops.health import run_all
    results = run_all()
    table = Table(title="Sentinel health check")
    table.add_column("Check"); table.add_column("Status"); table.add_column("Detail")
    failed = 0
    for r in results:
        table.add_row(r.name, "[green]OK[/green]" if r.ok else "[red]FAIL[/red]", r.detail)
        if not r.ok:
            failed += 1
    console.print(table)
    if failed:
        rprint(f"\n[red]{failed} check(s) failed.[/red] Fix above and re-run `sentinel doctor`.")
        raise typer.Exit(code=1)
    rprint("\n[green]All checks passed. Ready to `sentinel dashboard` or `sentinel scan`.[/green]")


@app.command()
def scan() -> None:
    """Run one full scan against the watchlist."""
    cfg, clock, _, _ = _bootstrap()
    from sentinel.analytics.performance import snapshot_today
    from sentinel.strategy import scanner
    recs = scanner.run_scan(cfg, cfg.storage.db_path, clock)
    snapshot_today(cfg.storage.db_path, starting_equity=cfg.app.initial_virtual_equity)
    rprint(f"[green]Scan complete[/green]: {len(recs)} qualifying recommendations.")
    for r in recs:
        rprint(
            f"  • [bold]{r.symbol}[/bold] score={r.score:.2f} "
            f"signals={','.join(s.value for s in r.signals_fired)}"
        )


@app.command()
def run(
    interval: str = typer.Option(
        "5m",
        "--interval", "-i",
        help="Scan cadence. Examples: 30s, 2m, 5m, 15m, 1h",
    ),
    market_hours_only: bool = typer.Option(
        True,
        help="If true, skip scans outside regular NYSE hours (9:30-16:00 ET) — still runs the sweep to enforce profit-take/stop on any open positions.",
    ),
) -> None:
    """Supervisor loop — scan every N and sweep open positions.

    Runs until Ctrl-C. Safer than cron for interactive use (you see the output
    live, and SIGINT cleanly stops the loop). Safe to run alongside the
    dashboard — SQLite WAL mode handles concurrent access.
    """
    import signal as _signal
    import time as _time

    cfg, clock, broker, _ = _bootstrap()
    from sentinel.analytics.performance import snapshot_today
    from sentinel.strategy import scanner

    seconds = _parse_interval(interval)
    rprint(
        f"[green]sentinel run[/green] started · interval={interval} "
        f"({seconds}s) · market_hours_only={market_hours_only} · Ctrl-C to stop."
    )

    stop = {"flag": False}
    def _sigint(_signum: int, _frame: object) -> None:
        stop["flag"] = True
        rprint("\n[yellow]Stop requested — finishing current cycle…[/yellow]")
    _signal.signal(_signal.SIGINT, _sigint)

    while not stop["flag"]:
        cycle_start = _time.monotonic()
        in_session = clock.is_market_open()
        ts = clock.now().strftime("%Y-%m-%d %H:%M:%S ET")
        try:
            # Always run the sweep — profit-take/auto-stop needs to fire even in ETH.
            closed = broker.sweep_open_positions()
            if closed:
                rprint(
                    f"[{ts}] [green]swept[/green]: auto-closed "
                    f"{len(closed)} position(s) — "
                    + ", ".join(f"{f.symbol}" for f in closed)
                )

            if market_hours_only and not in_session:
                rprint(f"[{ts}] [dim]market closed — skipping scan[/dim]")
            else:
                recs = scanner.run_scan(cfg, cfg.storage.db_path, clock)
                snapshot_today(cfg.storage.db_path, starting_equity=cfg.app.initial_virtual_equity)
                rprint(f"[{ts}] scan → [bold]{len(recs)}[/bold] rec(s)"
                       + (": " + ", ".join(r.symbol for r in recs) if recs else ""))
        except Exception as e:  # noqa: BLE001 — never let one cycle kill the loop
            rprint(f"[{ts}] [red]cycle error[/red]: {e}")

        if stop["flag"]:
            break
        elapsed = _time.monotonic() - cycle_start
        sleep_s = max(1.0, seconds - elapsed)
        # Chunked sleep so Ctrl-C is responsive
        slept = 0.0
        while slept < sleep_s and not stop["flag"]:
            _time.sleep(min(1.0, sleep_s - slept))
            slept += 1.0

    rprint("[green]Stopped.[/green]")


def _parse_interval(s: str) -> int:
    """Parse '30s' / '2m' / '1h' → seconds. Bare number = seconds."""
    s = s.strip().lower()
    if not s:
        raise typer.BadParameter("empty interval")
    unit = s[-1]
    try:
        if unit == "s":
            return int(s[:-1])
        if unit == "m":
            return int(s[:-1]) * 60
        if unit == "h":
            return int(s[:-1]) * 3600
        return int(s)   # bare seconds
    except ValueError as e:
        raise typer.BadParameter(f"can't parse interval {s!r}; use e.g. 30s, 5m, 1h") from e


@app.command()
def dashboard(
    port: int = typer.Option(8501, help="Streamlit port"),
    host: str = typer.Option("127.0.0.1", help="Bind host"),
) -> None:
    """Launch the Streamlit UI."""
    _bootstrap()
    app_path = Path(__file__).parent / "dashboard" / "app.py"
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app_path),
        "--server.port", str(port),
        "--server.address", host,
        "--browser.gatherUsageStats", "false",
    ]
    rprint(f"[green]Starting dashboard at http://{host}:{port}[/green]")
    os.execvp(cmd[0], cmd)


@app.command(name="list")
def list_positions() -> None:
    """List current open positions and account state."""
    cfg, _, broker, _ = _bootstrap()
    acct = broker.account()
    rprint(
        f"[bold]Account[/bold] (mode={acct.mode}): "
        f"equity={acct.equity}  cash={acct.cash}  "
        f"deployed={acct.deployed_pct:.0%}  open={acct.open_positions}/{cfg.risk.max_concurrent_positions}"
    )
    open_pos = positions.list_open(cfg.storage.db_path)
    if not open_pos:
        rprint("(no open positions)")
        return
    table = Table(title="Open positions")
    for h in ["Symbol", "Qty", "Avg entry", "Stop", "TP", "Opened (UTC)"]:
        table.add_column(h)
    for p in open_pos:
        table.add_row(
            p["symbol"], str(p["qty"]), p["avg_entry_price"],
            p["stop_price"] or "—", p["take_profit"] or "trailing",
            p["opened_at"],
        )
    console.print(table)


@app.command()
def add(
    symbol: str = typer.Argument(..., help="Ticker to buy at last quote"),
    qty: int = typer.Option(..., "--qty", "-q", help="Share quantity"),
    stop_pct: float = typer.Option(0.01, help="Hard stop as % below entry"),
) -> None:
    """Manually buy a symbol (bypasses scanner; still passes RiskManager)."""
    cfg, clock, broker, risk = _bootstrap()
    from sentinel.data.market import yfinance_feed
    quote = yfinance_feed.latest_quote(symbol)
    if not quote:
        rprint(f"[red]No quote for {symbol}[/red]")
        raise typer.Exit(1)
    entry = quote.last
    stop = entry * Decimal(str(1 - stop_pct))
    intent = OrderIntent(
        symbol=symbol.upper(), side=Side.LONG,
        signals_fired=tuple(), sentiment_score=0.0,
        entry_price=entry, stop_price=stop, take_profit=None,
        bar_ts=clock.now(),
    )
    eq = compute_equity_state(cfg.storage.db_path, cfg.app.initial_virtual_equity, clock)
    decision = risk.evaluate(intent, eq)
    if not decision.approved:
        rprint(f"[red]RiskManager rejected[/red]: {decision.gate} — {'; '.join(decision.reasons)}")
        raise typer.Exit(1)
    # Honor the user's qty if it's smaller than RiskManager's clamp
    use_qty = min(qty, decision.quantity)
    fill = broker.submit_buy(
        symbol=symbol.upper(), qty=use_qty, entry_price=entry,
        stop_price=stop, take_profit=None, decision=decision,
        source_recommendation_id=None,
    )
    rprint(f"[green]Filled[/green] {fill.qty} {fill.symbol} @ {fill.price} on {fill.ts}")


@app.command()
def close(symbol: str = typer.Argument(..., help="Symbol to close")) -> None:
    """Close the open position for a symbol at market."""
    cfg, _, broker, _ = _bootstrap()
    from sentinel.data.market import yfinance_feed
    pos = positions.get_open_by_symbol(cfg.storage.db_path, symbol.upper())
    if not pos:
        rprint(f"[yellow]No open position in {symbol}[/yellow]")
        raise typer.Exit(1)
    quote = yfinance_feed.latest_quote(symbol.upper())
    mark = quote.last if quote else Decimal(pos["avg_entry_price"])
    fill = broker.submit_sell(
        symbol=symbol.upper(), position_id=pos["id"],
        exit_price=mark, close_reason="user",
    )
    rprint(f"[green]Closed[/green] {fill.qty} {fill.symbol} @ {fill.price} on {fill.ts}")


@app.command()
def sweep() -> None:
    """Run profit-take + auto-stop sweep on demand. Same as the dashboard auto-run."""
    cfg, _, broker, _ = _bootstrap()
    closed = broker.sweep_open_positions()
    if not closed:
        rprint("[dim]No positions hit profit-take or stop.[/dim]")
        return
    for f in closed:
        rprint(f"[green]Auto-closed[/green] {f.qty} {f.symbol} @ {f.price}")


@app.command()
def backtest(
    start: str = typer.Option(..., "--start", help="Start date YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="End date YYYY-MM-DD (inclusive)"),
    symbols: str = typer.Option(
        "",
        "--symbols", "-s",
        help="Comma-separated symbols. Default: current watchlist (excluding SPY/QQQ which are auto-added for regime/benchmark).",
    ),
    timeframe: str = typer.Option("5m", help="Bar timeframe: 1m | 5m | 15m | 1h | 1d"),
    variant: str = typer.Option(
        "baseline",
        "--variant", "-v",
        help="Strategy variant: baseline | trend_filter | atr_stops | gap_followthrough | combined",
    ),
    save_csv: bool = typer.Option(True, help="Write trades + equity_curve to backtests/runs/<ts>/"),
) -> None:
    """Run a backtest of the current strategy over a historical date range.

    Note: yfinance only serves intraday bars for the past ~60 days. For longer
    ranges use --timeframe 1d (signal sensitivity is much lower on daily bars).
    """
    from datetime import date as _date
    from pathlib import Path as _Path
    cfg, _, _, _ = _bootstrap()
    universe: list[str] = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if symbols else watchlist.list_all(cfg.storage.db_path)
    )
    if not universe:
        rprint("[red]Empty watchlist and no --symbols given[/red]")
        raise typer.Exit(1)
    try:
        start_d = _date.fromisoformat(start)
        end_d = _date.fromisoformat(end)
    except ValueError as e:
        rprint(f"[red]Bad date: {e}[/red]")
        raise typer.Exit(1)

    from sentinel.backtest.variants import VARIANTS
    if variant not in VARIANTS:
        rprint(f"[red]Unknown variant {variant!r}. Choices: {list(VARIANTS.keys())}[/red]")
        raise typer.Exit(1)
    v = VARIANTS[variant]

    rprint(f"[green]Backtesting[/green] {len(universe)} symbols on {timeframe} from {start_d} to {end_d}")
    rprint(f"  variant: [bold]{v.name}[/bold] — {v.description}")
    rprint(f"  universe: {', '.join(universe)}")

    from sentinel.backtest.harness import run_backtest
    report = run_backtest(cfg, cfg.storage.db_path, universe, start_d, end_d, timeframe=timeframe, variant=v)

    # Persist trades so the dashboard can use them for forecasts
    import hashlib
    import uuid as _uuid
    from sentinel.storage.repos import backtest_trades
    config_hash = hashlib.sha256(repr(cfg.model_dump()).encode()).hexdigest()[:12]
    run_id = _uuid.uuid4().hex[:12]
    saved = backtest_trades.insert_run(
        cfg.storage.db_path,
        run_id=run_id, config_hash=config_hash, timeframe=timeframe,
        backtest_start=start_d, backtest_end=end_d,
        trades=report.trades,
    )
    rprint(f"  [dim]Saved {saved} trades to backtest_trades (run_id={run_id})[/dim]")

    # ---------- print summary ----------
    rprint()
    rprint(f"[bold]=== Backtest summary ({report.start} → {report.end}) ===[/bold]")
    rprint(f"  Initial equity: ${report.initial_equity:,.2f}")
    rprint(f"  Final equity:   ${report.final_equity:,.2f}")
    rprint(f"  Total return:   {report.total_return_pct:+.2%}   CAGR: {report.cagr_pct:+.2%}")
    rprint(f"  Sharpe: {report.sharpe:.2f}    Sortino: {report.sortino:.2f}    Max DD: {report.max_drawdown_pct:.2%}")
    rprint(f"  Trades: {report.n_trades}    Win rate: {report.win_rate:.0%}    Avg win: {_pct(report.avg_win_pct)}    Avg loss: {_pct(report.avg_loss_pct)}    PF: {_pf(report.profit_factor)}")
    rprint(f"  Avg holding: {report.avg_holding_minutes:.0f} min")
    rprint()
    rprint(f"  SPY return:   {report.spy_total_return_pct:+.2%}   SPY Sharpe: {report.spy_sharpe:.2f}   SPY MDD: {report.spy_max_drawdown_pct:.2%}")
    rprint(f"  Alpha vs SPY: {report.alpha_vs_spy_pct:+.2%}")
    color = {"BEATS_SPY":"green","TIES_SPY":"yellow","LOSES_TO_SPY":"red","INCONCLUSIVE":"white"}[report.verdict()]
    rprint(f"  [bold {color}]Verdict: {report.verdict()}[/bold {color}]")
    rprint()
    rprint(f"  Bars evaluated: {report.bars_evaluated:,}")
    rprint(f"  Signals fired (count over backtest): {report.signals_fired_counts}")
    rprint(f"  Closes by reason: {report.closes_by_reason}")
    if report.rejection_counts:
        top = sorted(report.rejection_counts.items(), key=lambda x: -x[1])[:5]
        rprint(f"  Top rejections: {dict(top)}")

    if save_csv:
        import csv
        from datetime import datetime as _dt
        out_dir = _Path("backtests/runs") / _dt.now().strftime("%Y%m%d-%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / "trades.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol","qty","entry_price","exit_price","entry_ts","exit_ts","pnl","pnl_pct","reason","signals"])
            for t in report.trades:
                w.writerow([t.symbol, t.qty, t.entry_price, t.exit_price,
                            t.entry_ts.isoformat(), t.exit_ts.isoformat(),
                            t.realized_pnl, f"{t.realized_pnl_pct:.4f}",
                            t.close_reason, "|".join(t.signals)])
        with (out_dir / "equity_curve.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts","equity"])
            for ts, eq in report.equity_curve:
                w.writerow([ts.isoformat(), eq])
        rprint(f"  [dim]Saved to {out_dir}/[/dim]")


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.2%}"


def _pf(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}"


@app.command()
def report(
    window: str = typer.Option("1mo", help="1w | 2w | 3w | 1mo | 3mo | 6mo | 1y | inception"),
) -> None:
    """Print performance metrics for a window."""
    cfg, _, _, _ = _bootstrap()
    from sentinel.analytics.performance import compute_window_metrics
    m = compute_window_metrics(cfg.storage.db_path, window)  # type: ignore[arg-type]
    if m is None:
        rprint("[yellow]Not enough data yet. Run `sentinel scan` for several sessions first.[/yellow]")
        return
    rprint(f"[bold]Window {window}[/bold]  ({m.start_date} → {m.end_date}, {m.days} days)")
    rprint(f"  Trades: {m.trades_in_window}  Win rate: {m.portfolio_win_rate}")
    rprint(f"  Portfolio: total {m.portfolio_total_return:+.2%}  Sharpe {m.portfolio_sharpe:.2f}  "
           f"Sortino {m.portfolio_sortino:.2f}  MDD {m.portfolio_max_drawdown:.2%}")
    rprint(f"  SPY      : total {m.spy_total_return:+.2%}  Sharpe {m.spy_sharpe:.2f}  MDD {m.spy_max_drawdown:.2%}")
    rprint(f"  Alpha vs SPY: {m.alpha_vs_spy:+.2%}   IR: {m.information_ratio}   Beta: {m.beta_vs_spy}")
    color = {"BEATS_SPY": "green", "TIES_SPY": "yellow", "LOSES_TO_SPY": "red", "INCONCLUSIVE": "white"}[m.verdict]
    rprint(f"  [bold {color}]Verdict: {m.verdict}[/bold {color}]")


# ---------- watchlist subcommands ----------

@watch_app.command("list")
def watch_list() -> None:
    cfg, _, _, _ = _bootstrap()
    syms = watchlist.list_all(cfg.storage.db_path)
    rprint(f"[bold]Watchlist ({len(syms)})[/bold]: {', '.join(syms)}")


@watch_app.command("add")
def watch_add(symbols: list[str] = typer.Argument(...)) -> None:
    cfg, _, _, _ = _bootstrap()
    for s in symbols:
        watchlist.add(cfg.storage.db_path, s)
    rprint(f"[green]Added[/green]: {', '.join(s.upper() for s in symbols)}")


@watch_app.command("remove")
def watch_remove(symbol: str) -> None:
    cfg, _, _, _ = _bootstrap()
    watchlist.remove(cfg.storage.db_path, symbol)
    rprint(f"[green]Removed[/green]: {symbol.upper()}")


# ---------- catalysts subcommands ----------

@catalysts_app.command("refresh")
def catalysts_refresh(
    days_ahead: int = typer.Option(14, help="How many days into the future to fetch"),
) -> None:
    """Pull upcoming earnings + IPO + macro events from Finnhub into the cache."""
    cfg, _, _, _ = _bootstrap()
    from sentinel.data.catalysts import refresh_calendar
    counts = refresh_calendar(cfg.storage.db_path, days_ahead=days_ahead)
    rprint(f"[green]Catalyst refresh[/green]: {counts}")


@catalysts_app.command("list")
def catalysts_list(
    days: int = typer.Option(7, help="Lookahead window in days"),
) -> None:
    """Print upcoming catalysts."""
    cfg, _, _, _ = _bootstrap()
    from sentinel.storage.repos import catalysts as _cat
    events = _cat.list_upcoming(cfg.storage.db_path, days=days)
    if not events:
        rprint("[dim]No catalysts cached. Run `sentinel catalysts refresh` first.[/dim]")
        return
    table = Table(title=f"Upcoming catalysts (next {days}d)")
    for h in ["Date", "Symbol", "Type", "When", "Title"]:
        table.add_column(h)
    for e in events:
        table.add_row(e["event_date"], e["symbol"], e["catalyst_type"],
                      (e["event_time"] or "").upper(), e["title"] or "")
    console.print(table)


# ---------- keychain subcommands ----------

@keychain_app.command("set")
def keychain_set(
    name: str = typer.Argument(..., help="Secret name: newsapi | finnhub | alpaca_key_id | alpaca_secret"),
    value: str = typer.Argument(..., help="The API key / secret value"),
) -> None:
    """Store a secret in macOS Keychain (service=sentinel). Overwrites if exists."""
    from sentinel.ops.keychain import known_secret_names, set_secret
    if name not in known_secret_names():
        rprint(f"[yellow]Warning[/yellow]: {name!r} not in known list {known_secret_names()}; storing anyway")
    set_secret(name, value)
    rprint(f"[green]Stored[/green] secret {name!r} in macOS Keychain (service=sentinel)")


@keychain_app.command("remove")
def keychain_remove(name: str) -> None:
    """Delete a secret from Keychain. Silent if absent."""
    from sentinel.ops.keychain import remove_secret
    remove_secret(name)
    rprint(f"[green]Removed[/green] secret {name!r}")


@keychain_app.command("list")
def keychain_list() -> None:
    """Show which known secrets are configured (values masked)."""
    from sentinel.ops.keychain import get_secret, known_secret_names
    table = Table(title="Keychain secrets")
    for h in ["Name", "Set?", "Preview"]:
        table.add_column(h)
    for name in known_secret_names():
        v = get_secret(name)
        if v:
            preview = v[:4] + "…" + v[-4:] if len(v) > 8 else "***"
            table.add_row(name, "[green]yes[/green]", preview)
        else:
            table.add_row(name, "[dim]no[/dim]", "—")
    console.print(table)


if __name__ == "__main__":
    app()
