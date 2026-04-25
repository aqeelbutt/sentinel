"""Macro event calendar (FOMC / CPI / NFP).

Hardcoded for 2026 — replace with a fetcher (FRED, BLS) once we have
infrastructure for it. For now, a static list keeps the dependency surface
small and is easy to verify by eye.

Convention: events are date-only (release time is per-event); the regime
gate combines this with a per-event known release time and the user's
configured cooldown_min to decide when trading can resume.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MacroEvent:
    name: str             # "FOMC" | "CPI" | "NFP"
    on: date
    release_time_et: time  # ET wall time of release


# Release times (US-typical):
#   FOMC statement: 14:00 ET
#   CPI:            08:30 ET
#   NFP (jobs):     08:30 ET on first Friday of month
_FOMC_TIME = time(14, 0)
_CPI_TIME = time(8, 30)
_NFP_TIME = time(8, 30)


# 2026 schedule (FOMC + CPI). NFP is computed as first Friday of each month.
_2026_EVENTS: list[MacroEvent] = [
    # FOMC meetings (8 per year, 2nd day = decision)
    MacroEvent("FOMC", date(2026, 1, 28), _FOMC_TIME),
    MacroEvent("FOMC", date(2026, 3, 18), _FOMC_TIME),
    MacroEvent("FOMC", date(2026, 4, 29), _FOMC_TIME),
    MacroEvent("FOMC", date(2026, 6, 17), _FOMC_TIME),
    MacroEvent("FOMC", date(2026, 7, 29), _FOMC_TIME),
    MacroEvent("FOMC", date(2026, 9, 16), _FOMC_TIME),
    MacroEvent("FOMC", date(2026, 11, 4), _FOMC_TIME),
    MacroEvent("FOMC", date(2026, 12, 16), _FOMC_TIME),
    # CPI releases (typically 2nd Tuesday/Wednesday of the month)
    MacroEvent("CPI", date(2026, 1, 14), _CPI_TIME),
    MacroEvent("CPI", date(2026, 2, 11), _CPI_TIME),
    MacroEvent("CPI", date(2026, 3, 12), _CPI_TIME),
    MacroEvent("CPI", date(2026, 4, 14), _CPI_TIME),
    MacroEvent("CPI", date(2026, 5, 13), _CPI_TIME),
    MacroEvent("CPI", date(2026, 6, 11), _CPI_TIME),
    MacroEvent("CPI", date(2026, 7, 14), _CPI_TIME),
    MacroEvent("CPI", date(2026, 8, 12), _CPI_TIME),
    MacroEvent("CPI", date(2026, 9, 11), _CPI_TIME),
    MacroEvent("CPI", date(2026, 10, 14), _CPI_TIME),
    MacroEvent("CPI", date(2026, 11, 12), _CPI_TIME),
    MacroEvent("CPI", date(2026, 12, 10), _CPI_TIME),
]


def _first_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    # weekday(): Mon=0 .. Sun=6 ; Friday=4
    return date(year, month, 1 + ((4 - d.weekday()) % 7))


def _nfp_dates(year: int) -> list[MacroEvent]:
    return [MacroEvent("NFP", _first_friday(year, m), _NFP_TIME) for m in range(1, 13)]


def get_event_for_date(d: date) -> MacroEvent | None:
    """Return the first matching event for date `d`, or None."""
    all_events = list(_2026_EVENTS) + _nfp_dates(d.year)
    for ev in all_events:
        if ev.on == d:
            return ev
    return None


def event_release_dt(ev: MacroEvent) -> datetime:
    return datetime.combine(ev.on, ev.release_time_et, tzinfo=NY)
