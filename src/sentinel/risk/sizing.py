"""Fixed-fractional position sizing by stop distance.

Given equity, risk_per_trade_pct, and the entry/stop prices, compute the
maximum integer quantity that keeps the loss at `stop_price` within the
per-trade risk budget.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal


def size_by_stop(
    *,
    equity: Decimal,
    risk_per_trade_pct: float,
    entry_price: Decimal,
    stop_price: Decimal,
) -> int:
    """Return quantity (whole shares). 0 if inputs invalid."""
    if entry_price <= 0 or stop_price <= 0 or stop_price >= entry_price:
        return 0
    per_share_risk = entry_price - stop_price
    risk_budget = equity * Decimal(str(risk_per_trade_pct))
    raw = risk_budget / per_share_risk
    return int(raw.quantize(Decimal("1"), rounding=ROUND_DOWN))


def clamp_by_position_cap(
    *,
    qty: int,
    entry_price: Decimal,
    equity: Decimal,
    max_per_position_pct: float,
) -> int:
    if qty <= 0:
        return 0
    max_notional = equity * Decimal(str(max_per_position_pct))
    max_qty = int((max_notional / entry_price).quantize(Decimal("1"), rounding=ROUND_DOWN))
    return min(qty, max_qty)


def clamp_by_deployed_cap(
    *,
    qty: int,
    entry_price: Decimal,
    currently_deployed: Decimal,
    equity: Decimal,
    max_deployed_pct: float,
) -> int:
    if qty <= 0:
        return 0
    cap = equity * Decimal(str(max_deployed_pct))
    remaining = max(Decimal("0"), cap - currently_deployed)
    if remaining <= 0:
        return 0
    max_qty = int((remaining / entry_price).quantize(Decimal("1"), rounding=ROUND_DOWN))
    return min(qty, max_qty)
