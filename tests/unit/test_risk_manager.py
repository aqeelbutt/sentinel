"""RiskManager — gate ordering and approval/rejection mechanics."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sentinel.core.clock import FixedClock, MarketClock
from sentinel.core.types import OrderIntent, Side
from sentinel.risk.manager import EquityState, Gate, HaltScope, RiskManager
from sentinel.storage.repos import halts, positions


def _intent() -> OrderIntent:
    return OrderIntent(
        symbol="AAPL", side=Side.LONG,
        signals_fired=tuple(), sentiment_score=0.5,
        entry_price=Decimal("100"), stop_price=Decimal("99"), take_profit=None,
        bar_ts=datetime.now(timezone.utc),
    )


def _eq(equity: float = 100000) -> EquityState:
    e = Decimal(str(equity))
    return EquityState(equity=e, cash=e, deployed=Decimal("0"), session_open_equity=e)


def _rm(cfg, tmp_db: Path) -> RiskManager:
    cfg.storage.db_path = tmp_db
    clk = MarketClock(source=FixedClock(datetime(2026, 4, 23, 14, 0, tzinfo=timezone.utc)))
    return RiskManager(cfg, tmp_db, clk)


def test_killed_halt_rejects_first(cfg, tmp_db: Path) -> None:
    halts.set_halt(tmp_db, HaltScope.ALL.value, "manual kill")
    rm = _rm(cfg, tmp_db)
    d = rm.evaluate(_intent(), _eq())
    assert d.approved is False
    assert d.gate == Gate.KILLED


def test_daily_loss_halts_new_entries(cfg, tmp_db: Path) -> None:
    rm = _rm(cfg, tmp_db)
    eq = EquityState(equity=Decimal("97500"), cash=Decimal("97500"),
                     deployed=Decimal("0"), session_open_equity=Decimal("100000"))
    d = rm.evaluate(_intent(), eq)
    assert d.approved is False
    assert d.gate == Gate.DAILY_LOSS


def test_concurrent_max_blocks_when_at_cap(cfg, tmp_db: Path) -> None:
    rm = _rm(cfg, tmp_db)
    # Create 5 open positions in different names.
    for s in ["AAA", "BBB", "CCC", "DDD", "EEE"]:
        positions.open_position(tmp_db, symbol=s, side=Side.LONG, qty=10,
                                avg_entry_price=Decimal("10"),
                                opened_at=datetime.now(timezone.utc),
                                stop_price=None, take_profit=None,
                                source_recommendation_id=None)
    d = rm.evaluate(_intent(), _eq())
    assert d.approved is False
    assert d.gate == Gate.CONCURRENT_MAX


def test_already_open_in_symbol_blocked(cfg, tmp_db: Path) -> None:
    rm = _rm(cfg, tmp_db)
    positions.open_position(tmp_db, symbol="AAPL", side=Side.LONG, qty=10,
                            avg_entry_price=Decimal("99"),
                            opened_at=datetime.now(timezone.utc),
                            stop_price=None, take_profit=None,
                            source_recommendation_id=None)
    d = rm.evaluate(_intent(), _eq())
    assert d.approved is False
    assert d.gate == Gate.CONCURRENT_MAX


def test_invalid_stop_blocked(cfg, tmp_db: Path) -> None:
    rm = _rm(cfg, tmp_db)
    bad = OrderIntent(symbol="AAPL", side=Side.LONG, signals_fired=tuple(),
                      sentiment_score=0.0, entry_price=Decimal("100"),
                      stop_price=Decimal("101"),  # stop above entry — invalid
                      take_profit=None, bar_ts=datetime.now(timezone.utc))
    d = rm.evaluate(bad, _eq())
    assert d.approved is False
    assert d.gate == Gate.PRICE_SANITY


def test_normal_intent_approved(cfg, tmp_db: Path) -> None:
    rm = _rm(cfg, tmp_db)
    d = rm.evaluate(_intent(), _eq())
    assert d.approved is True
    assert d.quantity > 0
    assert d.client_order_id != ""
