"""Time.

Two layers:

  `Clock`       — Protocol answering "what time is it?". Inject one into any
                  component that needs wall time. Live uses SystemClock; tests
                  and backtests use FixedClock or VirtualClock.

  `MarketClock` — NYSE-aware calendar + session bounds + exit windows. Uses a
                  Clock internally. All public methods return tz-aware
                  datetimes in America/New_York unless stated otherwise. DST
                  is handled by zoneinfo; early-close days are handled by
                  pandas_market_calendars.

Why the split: strategy/risk/execution code should never call `datetime.now()`
directly, because that makes backtests drift from live behavior and makes
tests non-deterministic. They should take a `Clock` and use it.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal
import pandas as pd

NY = ZoneInfo("America/New_York")
UTC = timezone.utc

_REGULAR_CLOSE = time(16, 0)
_EARLY_CLOSE = time(13, 0)
_OPEN = time(9, 30)


class Clock(Protocol):
    """Minimal time source. `now()` returns an aware datetime (timezone matters)."""
    def now(self) -> datetime: ...


class SystemClock:
    """Wraps the OS clock. Returns UTC; convert with .astimezone(NY) at the edges."""
    def now(self) -> datetime:
        return datetime.now(tz=UTC)


class FixedClock:
    """Returns a constant time. For unit tests."""
    def __init__(self, fixed: datetime) -> None:
        if fixed.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._fixed = fixed

    def now(self) -> datetime:
        return self._fixed


class VirtualClock:
    """Advances explicitly; used by the backtest harness as bars replay."""
    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("VirtualClock requires a timezone-aware start time")
        self._ts = start

    def now(self) -> datetime:
        return self._ts

    def advance_to(self, ts: datetime) -> None:
        if ts.tzinfo is None:
            raise ValueError("advance_to requires a timezone-aware datetime")
        if ts < self._ts:
            raise ValueError(f"Cannot advance clock backwards: {ts} < {self._ts}")
        self._ts = ts


class MarketClock:
    """NYSE-aware clock.

    Invariants (for any session date D):
        session_open(D) < moc_submission_deadline(D)
          < hard_liquidation_start(D) < session_close(D)

    Early-close days (from the NYSE calendar: day-after-Thanksgiving,
    July 3 when preceding a weekday July 4, Christmas Eve when a weekday)
    close at 13:00 ET; exit windows shift proportionally so the last 15
    minutes before close remain the hard-liquidation window.
    """

    def __init__(self, source: Clock | None = None, calendar_name: str = "XNYS") -> None:
        self._source = source or SystemClock()
        self._cal = mcal.get_calendar(calendar_name)

    # ---------- instantaneous queries ----------

    def now(self) -> datetime:
        """Current time in America/New_York."""
        raw = self._source.now()
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=UTC)
        return raw.astimezone(NY)

    def now_utc(self) -> datetime:
        raw = self._source.now()
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=UTC)
        return raw.astimezone(UTC)

    def is_session_day(self, on: date) -> bool:
        sched = self._cal.schedule(start_date=on, end_date=on)
        return len(sched) > 0

    def is_holiday(self, on: date) -> bool:
        # Monday-Friday but no schedule → holiday. Weekends aren't "holidays".
        if on.weekday() >= 5:
            return False
        return not self.is_session_day(on)

    def is_early_close(self, on: date) -> bool:
        sched = self._cal.schedule(start_date=on, end_date=on)
        if len(sched) == 0:
            return False
        close_et: pd.Timestamp = sched.iloc[0]["market_close"].tz_convert(NY)
        return close_et.time() < _REGULAR_CLOSE

    def is_market_open(self, ts: datetime | None = None) -> bool:
        t = self._ensure_et(ts or self.now())
        if not self.is_session_day(t.date()):
            return False
        return self.session_open(t.date()) <= t < self.session_close(t.date())

    def is_extended_hours(self, ts: datetime | None = None) -> bool:
        t = self._ensure_et(ts or self.now())
        if not self.is_session_day(t.date()):
            return False
        open_ = self.session_open(t.date())
        close = self.session_close(t.date())
        pre_open = open_.replace(hour=4, minute=0)
        post_close = close.replace(hour=20, minute=0)
        return pre_open <= t < open_ or close <= t < post_close

    # ---------- session bounds ----------

    def session_open(self, on: date | None = None) -> datetime:
        d = on or self.now().date()
        if not self.is_session_day(d):
            raise ValueError(f"{d} is not an NYSE session day")
        return datetime.combine(d, _OPEN, tzinfo=NY)

    def session_close(self, on: date | None = None) -> datetime:
        d = on or self.now().date()
        sched = self._cal.schedule(start_date=d, end_date=d)
        if len(sched) == 0:
            raise ValueError(f"{d} is not an NYSE session day")
        close_et: pd.Timestamp = sched.iloc[0]["market_close"].tz_convert(NY)
        return close_et.to_pydatetime()

    def next_session_open(self, after: datetime | None = None) -> datetime:
        t = self._ensure_et(after or self.now())
        # Check forward up to 10 days (covers long weekends and holiday clusters).
        sched = self._cal.schedule(start_date=t.date(), end_date=t.date() + timedelta(days=10))
        for _, row in sched.iterrows():
            open_et: pd.Timestamp = row["market_open"].tz_convert(NY)
            open_dt = open_et.to_pydatetime()
            if open_dt > t:
                return open_dt
        raise RuntimeError("No NYSE session found in next 10 days — calendar issue")

    def previous_session_close(self, before: datetime | None = None) -> datetime:
        t = self._ensure_et(before or self.now())
        sched = self._cal.schedule(start_date=t.date() - timedelta(days=10), end_date=t.date())
        for _, row in sched.iloc[::-1].iterrows():
            close_et: pd.Timestamp = row["market_close"].tz_convert(NY)
            close_dt = close_et.to_pydatetime()
            if close_dt < t:
                return close_dt
        raise RuntimeError("No NYSE session found in prior 10 days — calendar issue")

    # ---------- exit windows (early-close aware) ----------

    def soft_exit_start(self, on: date | None = None) -> datetime:
        """When to stop opening new intraday positions. 15 minutes before close."""
        close = self.session_close(on)
        return close - timedelta(minutes=15)

    def moc_submission_deadline(self, on: date | None = None) -> datetime:
        """Last moment to submit MOC orders (10 min before close)."""
        close = self.session_close(on)
        return close - timedelta(minutes=10)

    def hard_liquidation_start(self, on: date | None = None) -> datetime:
        """Flatten anything still open with market orders from here."""
        close = self.session_close(on)
        return close - timedelta(minutes=5)

    def is_in_soft_exit_window(self, ts: datetime | None = None) -> bool:
        t = self._ensure_et(ts or self.now())
        if not self.is_session_day(t.date()):
            return False
        return self.soft_exit_start(t.date()) <= t < self.session_close(t.date())

    def is_in_hard_liquidation_window(self, ts: datetime | None = None) -> bool:
        t = self._ensure_et(ts or self.now())
        if not self.is_session_day(t.date()):
            return False
        return self.hard_liquidation_start(t.date()) <= t < self.session_close(t.date())

    def time_until_close(self, ts: datetime | None = None) -> timedelta:
        t = self._ensure_et(ts or self.now())
        if not self.is_session_day(t.date()):
            return timedelta(0)
        return max(timedelta(0), self.session_close(t.date()) - t)

    # ---------- helpers ----------

    @staticmethod
    def _ensure_et(ts: datetime) -> datetime:
        if ts.tzinfo is None:
            raise ValueError("MarketClock requires timezone-aware datetimes")
        return ts.astimezone(NY)
