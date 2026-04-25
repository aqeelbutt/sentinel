"""MarketClock tests. No network, no I/O."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from sentinel.core.clock import FixedClock, MarketClock, NY


def _et(y: int, m: int, d: int, h: int = 12, mn: int = 0) -> datetime:
    return datetime(y, m, d, h, mn, tzinfo=NY)


def test_now_returns_aware_et_datetime() -> None:
    clk = MarketClock(source=FixedClock(_et(2026, 4, 23, 10, 30)))
    n = clk.now()
    assert n.tzinfo is not None
    assert n.utcoffset() is not None


def test_session_open_close_normal_day() -> None:
    clk = MarketClock(source=FixedClock(_et(2026, 4, 23, 10, 0)))  # Thursday
    so = clk.session_open(date(2026, 4, 23))
    sc = clk.session_close(date(2026, 4, 23))
    assert so.hour == 9 and so.minute == 30
    assert sc.hour == 16 and sc.minute == 0


def test_holiday_is_not_session() -> None:
    # 2026-01-01 is Thursday New Year's Day
    clk = MarketClock(source=FixedClock(_et(2026, 1, 1, 12, 0)))
    assert clk.is_holiday(date(2026, 1, 1)) is True
    assert clk.is_session_day(date(2026, 1, 1)) is False
    with pytest.raises(ValueError):
        clk.session_open(date(2026, 1, 1))


def test_weekend_is_not_holiday_but_no_session() -> None:
    clk = MarketClock(source=FixedClock(_et(2026, 4, 25, 12, 0)))  # Saturday
    assert clk.is_holiday(date(2026, 4, 25)) is False
    assert clk.is_session_day(date(2026, 4, 25)) is False


def test_dst_transition_session_still_930_et() -> None:
    # 2026-03-08 is the DST 'spring forward' Sunday; following Monday opens at 09:30 ET.
    clk = MarketClock(source=FixedClock(_et(2026, 3, 9, 8, 0)))
    so = clk.session_open(date(2026, 3, 9))
    assert so.hour == 9 and so.minute == 30
    assert so.utcoffset() == timedelta(hours=-4)  # EDT


def test_exit_window_normal_day() -> None:
    clk = MarketClock(source=FixedClock(_et(2026, 4, 23, 15, 50)))
    assert clk.is_in_soft_exit_window() is True       # 15:45 onwards
    assert clk.is_in_hard_liquidation_window() is False  # 15:55 onwards


def test_hard_liquidation_window_at_15_56() -> None:
    clk = MarketClock(source=FixedClock(_et(2026, 4, 23, 15, 56)))
    assert clk.is_in_hard_liquidation_window() is True


def test_next_session_open_skips_weekend() -> None:
    # Friday 5pm — next open should be Monday 9:30.
    clk = MarketClock(source=FixedClock(_et(2026, 4, 24, 17, 0)))
    nxt = clk.next_session_open()
    assert nxt.weekday() == 0  # Monday
    assert nxt.hour == 9 and nxt.minute == 30


def test_early_close_thanksgiving_friday_2026() -> None:
    # 2026-11-27 is the day after Thanksgiving — NYSE closes at 13:00 ET.
    clk = MarketClock()
    assert clk.is_session_day(date(2026, 11, 27)) is True
    assert clk.is_early_close(date(2026, 11, 27)) is True
    sc = clk.session_close(date(2026, 11, 27))
    assert sc.hour == 13


def test_time_until_close_outside_session_returns_zero() -> None:
    clk = MarketClock(source=FixedClock(_et(2026, 4, 25, 10, 0)))  # Saturday
    assert clk.time_until_close() == timedelta(0)
