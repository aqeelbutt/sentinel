"""Mean-reversion signal.

Fires when price has deviated far below the 20-period VWAP AND RSI is
oversold. The spec covers the short-side too (above VWAP + RSI overbought),
but shorts are disabled in Phase 1 (see config).
"""
from __future__ import annotations

from sentinel.config.schema import MeanReversionParams
from sentinel.core.types import SignalFiring, SignalName
from sentinel.strategy.indicators import rolling_vwap_std, rsi
from sentinel.strategy.signals.base import SignalContext


class MeanReversionSignal:
    def __init__(self, params: MeanReversionParams) -> None:
        self.p = params

    def name(self) -> str:
        return SignalName.MEAN_REVERSION.value

    def evaluate(self, ctx: SignalContext) -> SignalFiring:
        inputs: dict = {
            "vwap_window": self.p.vwap_window,
            "std_dev_threshold": self.p.std_dev_threshold,
            "rsi_window": self.p.rsi_window,
            "rsi_oversold": self.p.rsi_oversold,
        }

        if len(ctx.intraday_5m) < self.p.vwap_window + 1:
            return SignalFiring(
                name=SignalName.MEAN_REVERSION, symbol=ctx.symbol, fired=False,
                strength=0.0, ts=ctx.now, inputs=inputs,
            )

        v, s = rolling_vwap_std(ctx.intraday_5m, self.p.vwap_window)
        last = float(ctx.intraday_5m[-1].close)
        if not (s > 0):
            return SignalFiring(
                name=SignalName.MEAN_REVERSION, symbol=ctx.symbol, fired=False,
                strength=0.0, ts=ctx.now, inputs=inputs,
            )
        dev = (last - v) / s     # std devs from VWAP (negative = below)
        r = rsi(ctx.intraday_5m, self.p.rsi_window)
        inputs |= {"vwap": v, "std": s, "deviation_sigma": dev, "rsi": r, "last": last}

        fired_long = dev <= -self.p.std_dev_threshold and r <= self.p.rsi_oversold
        strength = 0.0
        if fired_long:
            dev_strength = min(1.0, abs(dev) / (self.p.std_dev_threshold * 2))
            rsi_strength = min(1.0, max(0.0, (self.p.rsi_oversold - r) / self.p.rsi_oversold))
            strength = 0.5 * dev_strength + 0.5 * rsi_strength
        return SignalFiring(
            name=SignalName.MEAN_REVERSION, symbol=ctx.symbol, fired=fired_long,
            strength=round(strength, 3), ts=ctx.now, inputs=inputs,
        )
