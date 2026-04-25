"""Position sizing math."""
from __future__ import annotations

from decimal import Decimal

from sentinel.risk.sizing import clamp_by_deployed_cap, clamp_by_position_cap, size_by_stop


def test_size_by_stop_basic() -> None:
    # 100k equity, 0.5% risk = 500. Entry 100, stop 99 → risk 1/share → 500 shares.
    qty = size_by_stop(equity=Decimal("100000"), risk_per_trade_pct=0.005,
                       entry_price=Decimal("100"), stop_price=Decimal("99"))
    assert qty == 500


def test_size_by_stop_invalid_returns_zero() -> None:
    assert size_by_stop(equity=Decimal("100000"), risk_per_trade_pct=0.005,
                       entry_price=Decimal("100"), stop_price=Decimal("100")) == 0
    assert size_by_stop(equity=Decimal("100000"), risk_per_trade_pct=0.005,
                       entry_price=Decimal("100"), stop_price=Decimal("101")) == 0


def test_position_cap_clamps_high_qty() -> None:
    # 25% cap on 100k = 25k notional max. At $100, cap = 250 shares.
    out = clamp_by_position_cap(qty=500, entry_price=Decimal("100"),
                                equity=Decimal("100000"), max_per_position_pct=0.25)
    assert out == 250


def test_deployed_cap_respects_existing_deployment() -> None:
    # 40% cap on 100k = 40k. Already deployed 30k → 10k remaining → 100 shares at $100.
    out = clamp_by_deployed_cap(qty=500, entry_price=Decimal("100"),
                                currently_deployed=Decimal("30000"),
                                equity=Decimal("100000"), max_deployed_pct=0.40)
    assert out == 100


def test_deployed_cap_zero_when_full() -> None:
    out = clamp_by_deployed_cap(qty=500, entry_price=Decimal("100"),
                                currently_deployed=Decimal("40000"),
                                equity=Decimal("100000"), max_deployed_pct=0.40)
    assert out == 0
