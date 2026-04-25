"""VirtualBroker — buy, sell, idempotency, profit-take sweep."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from sentinel.core.clock import FixedClock, MarketClock
from sentinel.core.identity import resolve_virtual_token
from sentinel.core.types import Quote
from sentinel.execution.virtual_broker import OrderRejected, VirtualBroker
from sentinel.risk.manager import RiskDecision
from sentinel.storage.repos import positions


def _decision(coid: str = "abc12345") -> RiskDecision:
    return RiskDecision(
        approved=True, quantity=10,
        stop_price=Decimal("99"), take_profit_price=None,
        gate=None, reasons=("ok",),
        evaluated_at=datetime.now(timezone.utc),
        intent_hash="x" * 16, client_order_id=coid,
    )


def _broker(cfg, db_path: Path) -> VirtualBroker:
    cfg.storage.db_path = db_path
    clk = MarketClock(source=FixedClock(datetime(2026, 4, 23, 14, 0, tzinfo=timezone.utc)))
    return VirtualBroker(cfg, db_path, clk, resolve_virtual_token())


def test_submit_buy_creates_open_position(cfg, tmp_db: Path) -> None:
    broker = _broker(cfg, tmp_db)
    fill = broker.submit_buy(
        symbol="AAPL", qty=10, entry_price=Decimal("100"),
        stop_price=Decimal("99"), take_profit=None,
        decision=_decision(), source_recommendation_id=None,
    )
    assert fill.qty == 10
    assert fill.symbol == "AAPL"
    open_pos = positions.list_open(tmp_db)
    assert len(open_pos) == 1
    assert open_pos[0]["symbol"] == "AAPL"


def test_submit_buy_rejects_unapproved(cfg, tmp_db: Path) -> None:
    broker = _broker(cfg, tmp_db)
    bad = RiskDecision(
        approved=False, quantity=0,
        stop_price=Decimal("99"), take_profit_price=None,
        gate=None, reasons=("test",), evaluated_at=datetime.now(timezone.utc),
        intent_hash="x" * 16, client_order_id="",
    )
    with pytest.raises(OrderRejected):
        broker.submit_buy(symbol="AAPL", qty=10, entry_price=Decimal("100"),
                          stop_price=Decimal("99"), take_profit=None,
                          decision=bad, source_recommendation_id=None)


def test_idempotent_resubmit_returns_original(cfg, tmp_db: Path) -> None:
    broker = _broker(cfg, tmp_db)
    d = _decision(coid="dedup0001")
    f1 = broker.submit_buy(symbol="MSFT", qty=10, entry_price=Decimal("400"),
                           stop_price=Decimal("396"), take_profit=None,
                           decision=d, source_recommendation_id=None)
    f2 = broker.submit_buy(symbol="MSFT", qty=10, entry_price=Decimal("400"),
                           stop_price=Decimal("396"), take_profit=None,
                           decision=d, source_recommendation_id=None)
    assert f1.client_order_id == f2.client_order_id
    assert f1.price == f2.price
    # Only one open position exists
    assert len(positions.list_open(tmp_db)) == 1


def test_submit_sell_marks_position_closed(cfg, tmp_db: Path) -> None:
    broker = _broker(cfg, tmp_db)
    f1 = broker.submit_buy(symbol="NVDA", qty=10, entry_price=Decimal("100"),
                           stop_price=Decimal("99"), take_profit=None,
                           decision=_decision(), source_recommendation_id=None)
    pos = positions.get_open_by_symbol(tmp_db, "NVDA")
    assert pos is not None
    f2 = broker.submit_sell(symbol="NVDA", position_id=pos["id"],
                            exit_price=Decimal("105"), close_reason="user")
    assert f2.qty == 10
    closed = positions.list_closed(tmp_db)
    assert len(closed) == 1
    assert closed[0]["close_reason"] == "user"


def test_profit_take_sweep_closes_winner(cfg, tmp_db: Path) -> None:
    broker = _broker(cfg, tmp_db)
    broker.submit_buy(symbol="SPY", qty=10, entry_price=Decimal("100"),
                      stop_price=Decimal("99"), take_profit=None,
                      decision=_decision(), source_recommendation_id=None)
    # Mock yfinance to return a quote 6% above entry
    fake_quote = Quote(symbol="SPY", ts=datetime.now(timezone.utc),
                       bid=Decimal("106"), ask=Decimal("106.01"), last=Decimal("106"))
    with patch("sentinel.execution.virtual_broker.yfinance_feed.latest_quote",
               return_value=fake_quote):
        closed = broker.sweep_open_positions()
    assert len(closed) == 1
    rows = positions.list_closed(tmp_db)
    assert rows[0]["close_reason"] == "profit_take"


def test_auto_stop_sweep_closes_loser(cfg, tmp_db: Path) -> None:
    broker = _broker(cfg, tmp_db)
    broker.submit_buy(symbol="QQQ", qty=10, entry_price=Decimal("100"),
                      stop_price=Decimal("99"), take_profit=None,
                      decision=_decision(), source_recommendation_id=None)
    fake_quote = Quote(symbol="QQQ", ts=datetime.now(timezone.utc),
                       bid=Decimal("98.5"), ask=Decimal("98.6"), last=Decimal("98.5"))
    with patch("sentinel.execution.virtual_broker.yfinance_feed.latest_quote",
               return_value=fake_quote):
        closed = broker.sweep_open_positions()
    assert len(closed) == 1
    rows = positions.list_closed(tmp_db)
    assert rows[0]["close_reason"] == "hard_stop"


def test_breakeven_stop_after_touching_plus_one_pct(cfg, tmp_db: Path) -> None:
    """Once mark touches +1%, peak is set; if mark falls back to entry, sell at break-even."""
    broker = _broker(cfg, tmp_db)
    broker.submit_buy(symbol="IBM", qty=10, entry_price=Decimal("100"),
                      stop_price=Decimal("99"), take_profit=None,
                      decision=_decision("be100001"), source_recommendation_id=None)

    # 1st sweep: price at +1.2% — under profit_take, should NOT close, but should set peak
    q1 = Quote(symbol="IBM", ts=datetime.now(timezone.utc),
               bid=Decimal("101.2"), ask=Decimal("101.21"), last=Decimal("101.2"))
    with patch("sentinel.execution.virtual_broker.yfinance_feed.latest_quote", return_value=q1):
        closed = broker.sweep_open_positions()
    assert closed == []
    assert len(positions.list_open(tmp_db)) == 1
    assert positions.list_open(tmp_db)[0]["peak_price"] == "101.2"

    # 2nd sweep: price falls back to entry — break-even stop fires
    q2 = Quote(symbol="IBM", ts=datetime.now(timezone.utc),
               bid=Decimal("100.0"), ask=Decimal("100.01"), last=Decimal("100.0"))
    with patch("sentinel.execution.virtual_broker.yfinance_feed.latest_quote", return_value=q2):
        closed = broker.sweep_open_positions()
    assert len(closed) == 1
    rows = positions.list_closed(tmp_db)
    assert rows[0]["close_reason"] == "breakeven_stop"


def test_trailing_stop_after_touching_plus_1_5_pct(cfg, tmp_db: Path) -> None:
    """Once peak >= +1.5%, trail at 0.75% below peak. Drop below the trail = exit."""
    broker = _broker(cfg, tmp_db)
    broker.submit_buy(symbol="ORCL", qty=10, entry_price=Decimal("100"),
                      stop_price=Decimal("99"), take_profit=None,
                      decision=_decision("trail0001"), source_recommendation_id=None)

    # 1st sweep: peak hits +2%
    q1 = Quote(symbol="ORCL", ts=datetime.now(timezone.utc),
               bid=Decimal("102"), ask=Decimal("102.01"), last=Decimal("102"))
    with patch("sentinel.execution.virtual_broker.yfinance_feed.latest_quote", return_value=q1):
        broker.sweep_open_positions()
    # 2nd sweep: drops to 102 * (1 - 0.0075) = 101.235 — still above trail floor
    q2 = Quote(symbol="ORCL", ts=datetime.now(timezone.utc),
               bid=Decimal("101.30"), ask=Decimal("101.31"), last=Decimal("101.30"))
    with patch("sentinel.execution.virtual_broker.yfinance_feed.latest_quote", return_value=q2):
        closed = broker.sweep_open_positions()
    assert closed == []  # still above trail
    # 3rd sweep: drops below trail floor (101.235)
    q3 = Quote(symbol="ORCL", ts=datetime.now(timezone.utc),
               bid=Decimal("101.10"), ask=Decimal("101.11"), last=Decimal("101.10"))
    with patch("sentinel.execution.virtual_broker.yfinance_feed.latest_quote", return_value=q3):
        closed = broker.sweep_open_positions()
    assert len(closed) == 1
    rows = positions.list_closed(tmp_db)
    assert rows[0]["close_reason"] == "trailing_stop"
