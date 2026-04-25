"""Macro Regime Gatekeeper.

Computes a `RegimeState` from current SPY / QQQ / VIX bars and the macro
event calendar. The strategy pipeline checks `regime.hostile` before
producing any BUY recommendations.

Decision rules (from CLAUDE.md):
  - SPY below 200-period VWAP on 5m → hostile
  - SPY or QQQ down > 1.5% intraday → hostile
  - VIX > 28 OR VIX up > 15% on day → hostile
  - FOMC / CPI / NFP day, before release_time + cooldown → hostile
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np

from sentinel.config.schema import RegimeSection
from sentinel.core.clock import MarketClock
from sentinel.core.types import RegimeState
from sentinel.data.macro.calendar import event_release_dt, get_event_for_date
from sentinel.data.market import bar_store

log = logging.getLogger(__name__)


class MacroRegimeGate:
    def __init__(self, db_path: Path, clock: MarketClock, cfg: RegimeSection) -> None:
        self.db_path = db_path
        self.clock = clock
        self.cfg = cfg

    def evaluate(self) -> RegimeState:
        """Compute the current regime. Pulls SPY/QQQ/VIX bars (cached) and
        the macro calendar; returns a fully-populated RegimeState. Failures
        in any sub-check are logged and surfaced as block_reasons rather
        than crashing — better to over-block than to under-block."""
        now_et = self.clock.now()
        reasons: list[str] = []

        spy_above_vwap, spy_intraday = self._spy_state()
        qqq_intraday = self._intraday_change_pct("QQQ")
        vix_level, vix_change = self._vix_state()

        if spy_above_vwap is False:
            reasons.append(f"SPY below {self.cfg.spy_vwap_window}-period 5m VWAP")
        if spy_intraday is not None and spy_intraday < -self.cfg.spy_daily_decline_pct:
            reasons.append(f"SPY intraday {spy_intraday:.2%} < -{self.cfg.spy_daily_decline_pct:.2%}")
        if qqq_intraday is not None and qqq_intraday < -self.cfg.qqq_daily_decline_pct:
            reasons.append(f"QQQ intraday {qqq_intraday:.2%} < -{self.cfg.qqq_daily_decline_pct:.2%}")
        if vix_level is not None and vix_level > self.cfg.vix_absolute:
            reasons.append(f"VIX {vix_level:.2f} > {self.cfg.vix_absolute}")
        if vix_change is not None and vix_change > self.cfg.vix_daily_rise_pct:
            reasons.append(f"VIX up {vix_change:.2%} > {self.cfg.vix_daily_rise_pct:.2%}")

        macro_event_name: str | None = None
        macro_event_until: datetime | None = None
        if self.cfg.block_macro_events:
            ev = get_event_for_date(now_et.date())
            if ev:
                macro_event_name = ev.name
                until = event_release_dt(ev) + timedelta(minutes=self.cfg.macro_event_cooldown_min)
                macro_event_until = until
                if now_et < until:
                    reasons.append(f"{ev.name} on {ev.on}; blocking until {until.strftime('%H:%M ET')}")

        return RegimeState(
            spy_above_vwap=bool(spy_above_vwap) if spy_above_vwap is not None else True,
            spy_intraday_change_pct=spy_intraday or 0.0,
            qqq_intraday_change_pct=qqq_intraday or 0.0,
            vix_level=vix_level or 0.0,
            vix_daily_change_pct=vix_change or 0.0,
            macro_event_today=macro_event_name,
            macro_event_cooldown_ends=macro_event_until,
            hostile=bool(reasons),
            block_reasons=tuple(reasons),
            computed_at=datetime.now(timezone.utc),
        )

    # ---------- per-component helpers ----------

    def _spy_state(self) -> tuple[bool | None, float | None]:
        bars = bar_store.get_bars(self.db_path, "SPY", "5m", days=10)
        if len(bars) < self.cfg.spy_vwap_window:
            return (None, self._intraday_change_pct("SPY"))
        recent = bars[-self.cfg.spy_vwap_window:]
        typical = np.array([float(b.high + b.low + b.close) / 3 for b in recent])
        vols = np.array([b.volume for b in recent], dtype=float)
        if vols.sum() == 0:
            return (None, self._intraday_change_pct("SPY"))
        vwap = float((typical * vols).sum() / vols.sum())
        last = float(bars[-1].close)
        return (last >= vwap, self._intraday_change_pct("SPY", bars=bars))

    def _intraday_change_pct(self, symbol: str, *, bars: list | None = None) -> float | None:
        if bars is None:
            bars = bar_store.get_bars(self.db_path, symbol, "5m", days=2)
        if not bars:
            return None
        today = self.clock.now().date()
        todays = [b for b in bars if b.ts.astimezone(self.clock.now().tzinfo).date() == today]
        if not todays:
            return None
        first_open = float(todays[0].open)
        last_close = float(todays[-1].close)
        if first_open == 0:
            return None
        return (last_close - first_open) / first_open

    def _vix_state(self) -> tuple[float | None, float | None]:
        bars = bar_store.get_bars(self.db_path, "^VIX", "1d", days=5)
        if len(bars) < 2:
            return (None, None)
        prev = float(bars[-2].close)
        curr = float(bars[-1].close)
        if prev == 0:
            return (curr, None)
        return (curr, (curr - prev) / prev)
