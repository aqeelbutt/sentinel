"""Formatting helpers — money, percentages, timestamps."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def money(v: Decimal | float | str | None, places: int = 2) -> str:
    if v is None or v == "":
        return "—"
    d = Decimal(str(v))
    sign = "-" if d < 0 else ""
    return f"{sign}${abs(d):,.{places}f}"


def pct(v: float | None, places: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v * 100:+.{places}f}%"


def fmt_ts(ts: str | datetime | None, *, with_seconds: bool = True) -> str:
    if ts is None or ts == "":
        return "—"
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            return ts
    else:
        dt = ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    et = dt.astimezone(NY)
    if with_seconds:
        return et.strftime("%Y-%m-%d %H:%M:%S ET")
    return et.strftime("%Y-%m-%d %H:%M ET")


def fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"
    days = int(seconds // 86400)
    return f"{days}d {int((seconds % 86400) // 3600)}h"


def trade_duration(opened_at_iso: str, closed_at_iso: str | None) -> str:
    if not closed_at_iso:
        return "—"
    o = datetime.fromisoformat(opened_at_iso)
    c = datetime.fromisoformat(closed_at_iso)
    return fmt_duration((c - o).total_seconds())
