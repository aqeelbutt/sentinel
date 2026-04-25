"""Strategy variants — research-backed augmentations to the baseline Triple-Check.

Each variant is a set of overrides applied during backtest. Lets us
A/B-test improvements against the baseline without polluting config or live code.

Variants implemented (all grounded in published trend-following / momentum
research):

  baseline          — current strategy as configured. Reference point.

  trend_filter      — require price > 50-period SMA before any long entry.
                      Classic momentum overlay; typically improves win rate
                      ~5-10pp on breakout strategies by removing
                      counter-trend trades.

  atr_stops         — replace fixed 1% stop with 1.5×ATR(14), and 5% target
                      with 3.0×ATR(14). Adapts each trade to the symbol's
                      natural volatility — a 1% stop on TSLA is noise, on KO
                      is a real signal. Improves R:R consistency.

  gap_followthrough — for Gap-and-Go specifically, require the bar AFTER the
                      open-gap to close above the gap level. Filters the
                      well-known "gap fade" pattern (~60% of gaps fade by
                      mid-day per Bulkowski et al).

  combined          — all three above. The hypothesis being tested is whether
                      these compound or overlap.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Variant:
    name: str
    description: str
    trend_filter_enabled: bool = False
    trend_filter_period: int = 50
    atr_stops_enabled: bool = False
    atr_stop_multiple: float = 1.5
    atr_target_multiple: float = 3.0
    gap_followthrough_enabled: bool = False


VARIANTS: dict[str, Variant] = {
    "baseline": Variant(
        name="baseline",
        description="Current strategy unchanged — reference point.",
    ),
    "trend_filter": Variant(
        name="trend_filter",
        description="Only take longs when price > SMA(50).",
        trend_filter_enabled=True,
    ),
    "atr_stops": Variant(
        name="atr_stops",
        description="Stops at 1.5×ATR, targets at 3.0×ATR — adaptive to volatility.",
        atr_stops_enabled=True,
    ),
    "gap_followthrough": Variant(
        name="gap_followthrough",
        description="Require post-gap bar to confirm above gap level.",
        gap_followthrough_enabled=True,
    ),
    "combined": Variant(
        name="combined",
        description="Trend filter + ATR stops + gap follow-through. The full augmented strategy.",
        trend_filter_enabled=True,
        atr_stops_enabled=True,
        gap_followthrough_enabled=True,
    ),
}
