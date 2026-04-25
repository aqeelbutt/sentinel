"""Liquidity pre-filter. Any symbol failing this is excluded before signals run."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from sentinel.config.schema import LiquidityParams
from sentinel.core.types import Bar, Quote
from sentinel.strategy.indicators import average_daily_volume


@dataclass(frozen=True)
class LiquidityResult:
    ok: bool
    reason: str | None
    adv: float
    spread_pct: float
    price: Decimal


def check(
    params: LiquidityParams,
    *,
    daily_bars: Sequence[Bar],
    quote: Quote,
) -> LiquidityResult:
    price = quote.last
    adv = average_daily_volume(daily_bars, days=20)
    spread = quote.spread_pct

    if price < params.min_price:
        return LiquidityResult(False, f"price {price} below min {params.min_price}", adv, spread, price)
    if adv < params.min_adv_shares:
        return LiquidityResult(False, f"ADV {adv:,.0f} below min {params.min_adv_shares:,}", adv, spread, price)
    if spread > params.max_spread_pct:
        return LiquidityResult(False, f"spread {spread:.4%} above max {params.max_spread_pct:.4%}", adv, spread, price)
    return LiquidityResult(True, None, adv, spread, price)
