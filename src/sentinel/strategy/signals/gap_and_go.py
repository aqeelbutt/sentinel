"""Gap-and-Go signal.

Fires when the stock opens with a large overnight gap AND unusual volume
in the early session. Strong intraday continuation pattern on a subset of
names; we require:
    overnight_gap >= min_gap_pct  (session open vs previous daily close)
    relative_volume >= min_rel_volume  (today's cumulative vol vs 20d ADV)
"""
from __future__ import annotations

from sentinel.config.schema import GapAndGoParams
from sentinel.core.types import SignalFiring, SignalName
from sentinel.strategy.indicators import average_daily_volume
from sentinel.strategy.signals.base import SignalContext


class GapAndGoSignal:
    def __init__(self, params: GapAndGoParams) -> None:
        self.p = params

    def name(self) -> str:
        return SignalName.GAP_AND_GO.value

    def evaluate(self, ctx: SignalContext) -> SignalFiring:
        inputs: dict = {"min_gap_pct": self.p.min_gap_pct, "min_rel_volume": self.p.min_rel_volume}

        if len(ctx.daily) < 2 or not ctx.intraday_5m:
            return SignalFiring(
                name=SignalName.GAP_AND_GO, symbol=ctx.symbol, fired=False,
                strength=0.0, ts=ctx.now, inputs=inputs,
            )

        # Filter intraday bars to TODAY only — the scanner passes ~10 days of bars
        # for VWAP/MR rolling windows, but Gap-and-Go needs today's session open
        # and today's cumulative volume. Without this filter, [0] would be a bar
        # from 10 days ago and the gap would be meaningless.
        today_et_date = ctx.now.astimezone(ctx.intraday_5m[0].ts.tzinfo).date()
        todays = [b for b in ctx.intraday_5m if b.ts.astimezone(ctx.now.tzinfo).date() == today_et_date]
        if not todays:
            inputs["reason"] = "no intraday bars yet for today"
            return SignalFiring(
                name=SignalName.GAP_AND_GO, symbol=ctx.symbol, fired=False,
                strength=0.0, ts=ctx.now, inputs=inputs,
            )

        prev_close = float(ctx.daily[-2].close)
        today_open = float(todays[0].open)
        if prev_close <= 0:
            return SignalFiring(
                name=SignalName.GAP_AND_GO, symbol=ctx.symbol, fired=False,
                strength=0.0, ts=ctx.now, inputs=inputs,
            )
        gap = (today_open - prev_close) / prev_close

        adv = average_daily_volume(ctx.daily, days=self.p.volume_lookback_days)
        session_vol = sum(b.volume for b in todays)
        # Elapsed-day fraction: bars since open / 78 bars per full RTH session
        day_fraction = max(0.05, min(1.0, len(todays) / 78.0))
        expected_vol = adv * day_fraction
        rel_vol = (session_vol / expected_vol) if expected_vol > 0 else 0.0

        inputs |= {"gap_pct": gap, "rel_vol": rel_vol, "session_vol": session_vol, "adv": adv}
        fired = gap >= self.p.min_gap_pct and rel_vol >= self.p.min_rel_volume
        strength = 0.0
        if fired:
            gap_ratio = min(1.0, gap / (self.p.min_gap_pct * 3))
            vol_ratio = min(1.0, rel_vol / (self.p.min_rel_volume * 2))
            strength = 0.5 * gap_ratio + 0.5 * vol_ratio
        return SignalFiring(
            name=SignalName.GAP_AND_GO, symbol=ctx.symbol, fired=fired,
            strength=round(strength, 3), ts=ctx.now, inputs=inputs,
        )
