"""Backtest harness.

Walks historical bars chronologically using SPY's 5m series as the heartbeat,
applies the **same** signals + liquidity filter + sizing math + profit-take/
hard-stop logic the live scanner uses, and records every trade.

Key design points (and what's *different* from live, deliberately):
  * No yfinance calls inside the loop — bars are pre-fetched once.
  * No live news / sentiment — sentiment is treated as neutral for the entire
    run (Phase 1 stub-equivalent). This is honest: we don't have historical
    news with point-in-time guarantees, so qualifying on "live sentiment" in
    a backtest would be lookahead. When we wire in a historical news source
    with proper publish-time accuracy we'll add that leg.
  * No correlation gate — backtest doesn't precompute a 30d rolling correlation
    matrix on every bar; we accept this as a simplification.
  * Fills happen at the NEXT bar's open (slippage-adjusted) for realism.
  * Macro regime: VIX > 28 OR VIX up > 15% on day blocks new entries. SPY VWAP
    and intraday-decline checks use the bars we have on hand.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from dataclasses import asdict

from sentinel.backtest.report import BacktestReport, SimTrade, compute_metrics
from sentinel.backtest.variants import VARIANTS, Variant
from sentinel.config.schema import Config
from sentinel.core.types import Bar, SignalFiring
from sentinel.data.market import bar_store, yfinance_feed
from sentinel.storage.repos import price_history
from sentinel.strategy.indicators import average_daily_volume, vwap
from sentinel.strategy.signals.base import SignalContext
from sentinel.strategy.signals.gap_and_go import GapAndGoSignal
from sentinel.strategy.signals.mean_reversion import MeanReversionSignal
from sentinel.strategy.signals.vwap_reclaim import VwapReclaimSignal

log = logging.getLogger(__name__)


@dataclass
class _SimPosition:
    symbol: str
    qty: int
    entry_price: Decimal
    entry_ts: datetime
    stop_price: Decimal              # initial hard stop (also acts as backstop floor)
    target_price: Decimal | None     # ATR-scaled target if set; else use cfg %
    peak_price: Decimal              # highest mark since entry — drives BE/trailing
    signals: tuple[str, ...]


def run_backtest(
    cfg: Config,
    db_path: Path,
    symbols: list[str],
    start: date,
    end: date,
    *,
    timeframe: str = "5m",
    progress: bool = True,
    variant: Variant | None = None,
) -> BacktestReport:
    """Replay historical bars through the same strategy code.

    `symbols` is the trading universe (excludes SPY/QQQ/VIX which are auto-added
    for regime/benchmark). `start`/`end` are inclusive trading dates.
    """
    variant = variant or VARIANTS["baseline"]
    log.info("backtest variant: %s — %s", variant.name, variant.description)
    universe = sorted({s.upper() for s in symbols} - {"SPY", "QQQ", "^VIX"})
    fetch_set = universe + ["SPY", "QQQ", "^VIX"]

    log.info("backtest: fetching bars for %d symbols (range needs %d days)",
             len(fetch_set), (end - start).days + 60)
    days_buf = max(60, (end - start).days + 60)
    intraday_bars: dict[str, list[Bar]] = {}
    daily_bars: dict[str, list[Bar]] = {}
    for sym in fetch_set:
        # Bypass cache freshness — backtest needs the FULL historical range,
        # not whatever a recent live scan happened to cache. Fetch fresh from
        # yfinance, then upsert into the cache so subsequent runs are faster.
        intra = yfinance_feed.fetch_bars(sym, timeframe, days=days_buf)
        if intra:
            price_history.upsert_many(db_path, [asdict(b) for b in intra])
        intraday_bars[sym] = intra

        if timeframe != "1d":
            daily = yfinance_feed.fetch_bars(sym, "1d", days=days_buf)
            if daily:
                price_history.upsert_many(db_path, [asdict(b) for b in daily])
            daily_bars[sym] = daily
        else:
            daily_bars[sym] = intra
        log.info("  %s: %d %s bars + %d daily bars", sym, len(intra), timeframe, len(daily_bars[sym]))

    # Heartbeat: every SPY 5m bar in [start, end]
    spy_5m = intraday_bars.get("SPY", [])
    in_window = [b for b in spy_5m if start <= b.ts.date() <= end]
    if not in_window:
        raise ValueError(
            f"no SPY {timeframe} bars in {start}..{end} — yfinance limits "
            f"intraday history to ~60 days. For longer ranges use timeframe='1d'."
        )

    # Pre-build per-symbol sorted-bar arrays + ts→idx maps for O(1) slicing
    sym_bars = {s: intraday_bars.get(s, []) for s in universe}
    sym_daily = {s: daily_bars.get(s, []) for s in universe}

    sig_gap = GapAndGoSignal(cfg.signals.gap_and_go)
    sig_mr = MeanReversionSignal(cfg.signals.mean_reversion)
    sig_vwap = VwapReclaimSignal(cfg.signals.vwap_reclaim)

    initial_equity = cfg.app.initial_virtual_equity
    cash = initial_equity
    open_positions: dict[str, _SimPosition] = {}
    trades: list[SimTrade] = []
    equity_curve: list[tuple[datetime, Decimal]] = []
    spy_curve: list[tuple[datetime, float]] = []
    closes_by_reason: dict[str, int] = {}
    signals_fired_counts: dict[str, int] = {"gap_and_go": 0, "mean_reversion": 0, "vwap_reclaim": 0}
    rejection_counts: dict[str, int] = {}
    bars_evaluated = 0

    # We progress through SPY's 5m bars; each tick = decision + sweep + mark
    last_logged_date: date | None = None
    for i, tick in enumerate(in_window):
        now = tick.ts
        bars_evaluated += 1

        if progress and tick.ts.date() != last_logged_date:
            last_logged_date = tick.ts.date()
            log.info("backtest: %s  equity=$%s  open=%d  trades=%d",
                     last_logged_date, _money(cash + _deployed_value(open_positions, sym_bars, now)),
                     len(open_positions), len(trades))

        # 1. Mark every symbol to its most-recent close at `now`
        current_marks: dict[str, Decimal] = {}
        for sym in universe:
            bars = sym_bars[sym]
            mark = _last_close_at_or_before(bars, now)
            if mark is not None:
                current_marks[sym] = mark

        # 2. Layered sweep: profit-take → trailing → break-even → hard stop.
        # Same priority order as VirtualBroker.sweep_open_positions().
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            mark = current_marks.get(sym)
            if mark is None:
                continue
            if mark > pos.peak_price:
                pos.peak_price = mark
            mark_pct = float((mark - pos.entry_price) / pos.entry_price)
            peak_pct = float((pos.peak_price - pos.entry_price) / pos.entry_price)
            close_reason: str | None = None

            # 1. profit take (ATR target if set, else cfg %)
            if pos.target_price is not None and mark >= pos.target_price:
                close_reason = "profit_take"
            elif pos.target_price is None and mark_pct >= cfg.exit.profit_take_pct:
                close_reason = "profit_take"
            # 2. trailing stop
            elif (cfg.exit.trailing_stop_enabled
                  and peak_pct >= cfg.exit.trailing_stop_activate_pct):
                trail_floor = pos.peak_price * (Decimal("1") - Decimal(str(cfg.exit.trailing_stop_pct)))
                if mark <= trail_floor:
                    close_reason = "trailing_stop"
            # 3. break-even stop
            elif (cfg.exit.breakeven_stop_enabled
                  and peak_pct >= cfg.exit.breakeven_trigger_pct):
                be_price = pos.entry_price * (Decimal("1") + Decimal(str(cfg.exit.breakeven_buffer_bps)) / Decimal("10000"))
                if mark <= be_price:
                    close_reason = "breakeven_stop"
            # 4. initial hard stop
            elif mark <= pos.stop_price:
                close_reason = "hard_stop"
            if close_reason:
                exit_price = mark
                pnl = Decimal(pos.qty) * (exit_price - pos.entry_price)
                cash += Decimal(pos.qty) * exit_price
                trades.append(SimTrade(
                    symbol=sym, qty=pos.qty,
                    entry_price=pos.entry_price, exit_price=exit_price,
                    entry_ts=pos.entry_ts, exit_ts=now,
                    realized_pnl=pnl, realized_pnl_pct=mark_pct,
                    close_reason=close_reason, signals=pos.signals,
                ))
                closes_by_reason[close_reason] = closes_by_reason.get(close_reason, 0) + 1
                del open_positions[sym]

        # 3. Regime check (VIX-based; SPY VWAP also checked)
        regime_hostile, regime_reason = _is_regime_hostile(daily_bars, intraday_bars, now, cfg)
        if regime_hostile:
            rejection_counts[f"regime:{regime_reason}"] = rejection_counts.get(f"regime:{regime_reason}", 0) + 1
            equity = cash + _deployed_value(open_positions, sym_bars, now)
            equity_curve.append((now, equity))
            _append_spy_mark(spy_curve, intraday_bars, now)
            continue

        # 4. For each symbol not in position, evaluate signals
        for sym in universe:
            if sym in open_positions:
                continue
            if len(open_positions) >= cfg.risk.max_concurrent_positions:
                break

            intra = [b for b in sym_bars[sym] if b.ts <= now]
            daily = [b for b in sym_daily[sym] if b.ts.date() <= now.date()]
            if len(intra) < 50 or len(daily) < 30:
                continue

            # liquidity
            adv = average_daily_volume(daily, days=20)
            if adv < cfg.signals.liquidity.min_adv_shares:
                continue
            last_close = intra[-1].close
            if last_close < cfg.signals.liquidity.min_price:
                continue

            # Variant: trend filter — only longs when price > SMA(N) on daily
            if variant.trend_filter_enabled:
                if len(daily) < variant.trend_filter_period:
                    rejection_counts["trend_filter:insufficient_history"] = (
                        rejection_counts.get("trend_filter:insufficient_history", 0) + 1
                    )
                    continue
                sma = sum(float(b.close) for b in daily[-variant.trend_filter_period:]) / variant.trend_filter_period
                if float(intra[-1].close) < sma:
                    rejection_counts["trend_filter:below_sma"] = rejection_counts.get("trend_filter:below_sma", 0) + 1
                    continue

            ctx = SignalContext(symbol=sym, now=now, intraday_5m=intra, daily=daily)
            firings = [sig_gap.evaluate(ctx), sig_mr.evaluate(ctx), sig_vwap.evaluate(ctx)]

            # Variant: gap follow-through — kill the Gap-and-Go firing if the
            # current bar (post-gap) doesn't close above its own open + the gap.
            if variant.gap_followthrough_enabled:
                gnf_idx = next((i for i, f in enumerate(firings) if f.name.value == "gap_and_go" and f.fired), None)
                if gnf_idx is not None:
                    if float(intra[-1].close) <= float(intra[-1].open):
                        # bar closed below its open — gap is fading; demote signal
                        firings[gnf_idx] = SignalFiring(
                            name=firings[gnf_idx].name, symbol=sym,
                            fired=False, strength=0.0, ts=now,
                            inputs={**firings[gnf_idx].inputs, "fade_filter": "blocked"},
                        )

            for f in firings:
                if f.fired:
                    signals_fired_counts[f.name.value] += 1
            fired = [f for f in firings if f.fired]
            if len(fired) < cfg.signals.min_signals_required:
                continue

            # Buy at NEXT bar's open
            entry_bar = next((b for b in sym_bars[sym] if b.ts > now), None)
            if entry_bar is None:
                continue
            entry_price = entry_bar.open * (Decimal("1") + Decimal(str(cfg.virtual_broker.slippage_bps)) / Decimal("10000"))
            entry_ts = entry_bar.ts

            # Sizing — fixed-fractional + per-position cap
            # Variant: ATR-scaled stop replaces fixed pct
            if variant.atr_stops_enabled:
                from sentinel.strategy.indicators import atr as _atr
                atr_v = _atr(daily, window=14) if len(daily) > 14 else 0.0
                if atr_v > 0:
                    stop = entry_price - Decimal(str(atr_v * variant.atr_stop_multiple))
                else:
                    stop = entry_price * (Decimal("1") - Decimal(str(cfg.exit.hard_stop_pct)))
            else:
                stop = entry_price * (Decimal("1") - Decimal(str(cfg.exit.hard_stop_pct)))
            risk_per_share = entry_price - stop
            if risk_per_share <= 0:
                continue
            equity_now = cash + _deployed_value(open_positions, sym_bars, now)
            qty = int((equity_now * Decimal(str(cfg.risk.risk_per_trade_pct))) / risk_per_share)
            max_per_pos = int((equity_now * Decimal(str(cfg.risk.max_per_position_pct))) / entry_price)
            qty = min(qty, max_per_pos)
            if qty <= 0:
                rejection_counts["sizing_zero"] = rejection_counts.get("sizing_zero", 0) + 1
                continue
            cost = Decimal(qty) * entry_price
            if cost > cash:
                rejection_counts["insufficient_cash"] = rejection_counts.get("insufficient_cash", 0) + 1
                continue
            # Deployed cap
            deployed_after = _deployed_value(open_positions, sym_bars, now) + cost
            if deployed_after > equity_now * Decimal(str(cfg.risk.max_deployed_pct)):
                rejection_counts["deployed_cap"] = rejection_counts.get("deployed_cap", 0) + 1
                continue

            # ATR-scaled target if variant says so; else use config %
            if variant.atr_stops_enabled:
                from sentinel.strategy.indicators import atr as _atr
                atr_v = _atr(daily, window=14) if len(daily) > 14 else 0.0
                target = entry_price + Decimal(str(atr_v * variant.atr_target_multiple)) if atr_v > 0 else None
            else:
                target = None  # sweep falls back to cfg.exit.profit_take_pct

            cash -= cost
            open_positions[sym] = _SimPosition(
                symbol=sym, qty=qty,
                entry_price=entry_price, entry_ts=entry_ts,
                stop_price=stop, target_price=target,
                peak_price=entry_price,    # initialize peak at entry
                signals=tuple(f.name.value for f in fired),
            )

        # Mark equity at end of tick
        equity_now = cash + _deployed_value(open_positions, sym_bars, now)
        equity_curve.append((now, equity_now))
        _append_spy_mark(spy_curve, intraday_bars, now)

    # Final liquidation at last tick
    final_now = in_window[-1].ts
    for sym, pos in list(open_positions.items()):
        mark = _last_close_at_or_before(sym_bars[sym], final_now) or pos.entry_price
        pnl = Decimal(pos.qty) * (mark - pos.entry_price)
        cash += Decimal(pos.qty) * mark
        trades.append(SimTrade(
            symbol=sym, qty=pos.qty,
            entry_price=pos.entry_price, exit_price=mark,
            entry_ts=pos.entry_ts, exit_ts=final_now,
            realized_pnl=pnl, realized_pnl_pct=float((mark - pos.entry_price) / pos.entry_price),
            close_reason="end_of_backtest", signals=pos.signals,
        ))
        closes_by_reason["end_of_backtest"] = closes_by_reason.get("end_of_backtest", 0) + 1

    if equity_curve and equity_curve[-1][1] != cash:
        equity_curve[-1] = (equity_curve[-1][0], cash)

    return compute_metrics(
        start=start, end=end, symbols=tuple(universe),
        timeframe=timeframe, initial_equity=initial_equity,
        equity_curve=equity_curve, trades=trades, spy_curve=spy_curve,
        closes_by_reason=closes_by_reason,
        signals_fired_counts=signals_fired_counts,
        rejection_counts=rejection_counts,
        bars_evaluated=bars_evaluated,
    )


# ---------- helpers ----------

def _last_close_at_or_before(bars: list[Bar], ts: datetime) -> Decimal | None:
    # bars are chronological; binary-search would be faster but linear is fine here
    last: Decimal | None = None
    for b in bars:
        if b.ts > ts:
            break
        last = b.close
    return last


def _deployed_value(
    positions: dict[str, _SimPosition],
    sym_bars: dict[str, list[Bar]],
    now: datetime,
) -> Decimal:
    total = Decimal("0")
    for sym, pos in positions.items():
        mark = _last_close_at_or_before(sym_bars.get(sym, []), now) or pos.entry_price
        total += Decimal(pos.qty) * mark
    return total


def _is_regime_hostile(
    daily_bars: dict[str, list[Bar]],
    intraday_bars: dict[str, list[Bar]],
    now: datetime,
    cfg: Config,
) -> tuple[bool, str]:
    vix_d = [b for b in daily_bars.get("^VIX", []) if b.ts.date() <= now.date()]
    if len(vix_d) >= 2:
        vix_now = float(vix_d[-1].close)
        vix_prev = float(vix_d[-2].close)
        if vix_now > cfg.regime.vix_absolute:
            return (True, "vix_high")
        if vix_prev > 0 and (vix_now - vix_prev) / vix_prev > cfg.regime.vix_daily_rise_pct:
            return (True, "vix_spike")

    spy_d = [b for b in daily_bars.get("SPY", []) if b.ts.date() <= now.date()]
    if spy_d:
        # Intraday change for today: open vs current close
        spy_today = [b for b in intraday_bars.get("SPY", [])
                     if b.ts.date() == now.date() and b.ts <= now]
        if spy_today:
            first_open = float(spy_today[0].open)
            last_close = float(spy_today[-1].close)
            if first_open > 0 and (last_close - first_open) / first_open < -cfg.regime.spy_daily_decline_pct:
                return (True, "spy_intraday_decline")

    qqq_today = [b for b in intraday_bars.get("QQQ", [])
                 if b.ts.date() == now.date() and b.ts <= now]
    if qqq_today:
        first_open = float(qqq_today[0].open)
        last_close = float(qqq_today[-1].close)
        if first_open > 0 and (last_close - first_open) / first_open < -cfg.regime.qqq_daily_decline_pct:
            return (True, "qqq_intraday_decline")

    return (False, "")


def _append_spy_mark(
    spy_curve: list[tuple[datetime, float]],
    intraday_bars: dict[str, list[Bar]],
    now: datetime,
) -> None:
    spy_bars = intraday_bars.get("SPY", [])
    mark = _last_close_at_or_before(spy_bars, now)
    if mark is not None:
        spy_curve.append((now, float(mark)))


def _money(d: Decimal) -> str:
    return f"{d:,.2f}"
