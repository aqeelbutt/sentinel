"""VWAP Reclaim signal.

Fires when price crosses above session VWAP with volume confirmation —
i.e. the previous bar's close was below session VWAP, the current bar's
close is above session VWAP, and current-bar volume is meaningfully
larger than the recent 20-bar average.
"""
from __future__ import annotations

import numpy as np

from sentinel.config.schema import VwapReclaimParams
from sentinel.core.types import SignalFiring, SignalName
from sentinel.strategy.indicators import vwap
from sentinel.strategy.signals.base import SignalContext


class VwapReclaimSignal:
    def __init__(self, params: VwapReclaimParams) -> None:
        self.p = params

    def name(self) -> str:
        return SignalName.VWAP_RECLAIM.value

    def evaluate(self, ctx: SignalContext) -> SignalFiring:
        inputs: dict = {"volume_confirmation_mult": self.p.volume_confirmation_mult}

        bars = list(ctx.intraday_5m)
        if len(bars) < 21:
            return SignalFiring(
                name=SignalName.VWAP_RECLAIM, symbol=ctx.symbol, fired=False,
                strength=0.0, ts=ctx.now, inputs=inputs,
            )

        # session VWAP over all intraday bars today
        today = ctx.now.date()
        todays = [b for b in bars if b.ts.astimezone(ctx.now.tzinfo).date() == today] or bars
        session_vwap = vwap(todays)

        prev = bars[-2]
        curr = bars[-1]
        avg_vol_20 = float(np.mean([b.volume for b in bars[-21:-1]])) or 1.0
        vol_ratio = curr.volume / avg_vol_20

        inputs |= {
            "session_vwap": session_vwap,
            "prev_close": float(prev.close),
            "curr_close": float(curr.close),
            "vol_ratio": vol_ratio,
        }

        crossed_up = float(prev.close) < session_vwap <= float(curr.close)
        fired = crossed_up and vol_ratio >= self.p.volume_confirmation_mult
        strength = 0.0
        if fired:
            cross_strength = min(1.0, (float(curr.close) - session_vwap) / max(session_vwap, 1e-9) * 100)
            vol_strength = min(1.0, vol_ratio / (self.p.volume_confirmation_mult * 2))
            strength = 0.5 * cross_strength + 0.5 * vol_strength
        return SignalFiring(
            name=SignalName.VWAP_RECLAIM, symbol=ctx.symbol, fired=fired,
            strength=round(strength, 3), ts=ctx.now, inputs=inputs,
        )
