"""Finnhub catalyst fetchers — earnings + IPO + dividend calendars.

Free tier: 60 req/min. One call covers a date range across all symbols, so
even a 30-day refresh is one request total. Cheap to run hourly.

Falls back to a static FOMC/CPI/NFP calendar from data.macro.calendar when
no Finnhub key is set, so the system has *some* catalyst data even without
the paid feed.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from sentinel.data.macro.calendar import _2026_EVENTS, _nfp_dates
from sentinel.ops.keychain import get_secret
from sentinel.storage.repos import catalysts as catalysts_repo

log = logging.getLogger(__name__)


def refresh_calendar(
    db_path: Path,
    *,
    days_ahead: int = 30,
    api_key: str | None = None,
) -> dict[str, int]:
    """Pull upcoming earnings + IPO + dividend events from Finnhub and store.

    Returns counts per catalyst_type. Always seeds macro events from the
    static calendar even if Finnhub is unavailable.
    """
    out = {"earnings": 0, "ipo": 0, "macro": 0}

    # 1. Always seed macro events (free, deterministic)
    out["macro"] = _seed_macro_events(db_path, days_ahead=days_ahead)

    # 2. Pull from Finnhub if key is set
    key = api_key or get_secret("finnhub")
    if not key:
        log.warning("finnhub key not set — skipping earnings/IPO calendar refresh; only macro events available")
        return out

    today = date.today()
    until = today + timedelta(days=days_ahead)
    headers = {"X-Finnhub-Token": key}
    with httpx.Client(timeout=15.0, headers=headers) as client:
        out["earnings"] = _fetch_earnings(db_path, client, today, until)
        out["ipo"] = _fetch_ipo(db_path, client, today, until)
    log.info("catalyst refresh complete: %s", out)
    return out


def _fetch_earnings(db_path: Path, client: httpx.Client, frm: date, to: date) -> int:
    try:
        r = client.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": frm.isoformat(), "to": to.isoformat()},
        )
        if r.status_code == 429:
            log.warning("finnhub earnings: rate-limited"); return 0
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("finnhub earnings fetch failed: %s", e)
        return 0
    items = data.get("earningsCalendar", []) if isinstance(data, dict) else []
    events = []
    for it in items:
        sym = it.get("symbol") or ""
        evt_date = it.get("date") or ""
        if not sym or not evt_date:
            continue
        try:
            d = date.fromisoformat(evt_date)
        except ValueError:
            continue
        events.append({
            "symbol": sym,
            "catalyst_type": "earnings",
            "event_date": d,
            "event_time": it.get("hour"),  # bmo | amc | dmh | ""
            "title": f"{sym} earnings ({it.get('quarter','?')}Q{it.get('year','')})",
            "payload": {
                "eps_estimate": it.get("epsEstimate"),
                "revenue_estimate": it.get("revenueEstimate"),
                "quarter": it.get("quarter"), "year": it.get("year"),
            },
        })
    return catalysts_repo.upsert_many(db_path, events)


def _fetch_ipo(db_path: Path, client: httpx.Client, frm: date, to: date) -> int:
    try:
        r = client.get(
            "https://finnhub.io/api/v1/calendar/ipo",
            params={"from": frm.isoformat(), "to": to.isoformat()},
        )
        if r.status_code == 429:
            log.warning("finnhub ipo: rate-limited"); return 0
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("finnhub ipo fetch failed: %s", e)
        return 0
    items = data.get("ipoCalendar", []) if isinstance(data, dict) else []
    events = []
    for it in items:
        sym = it.get("symbol") or ""
        d_str = it.get("date") or ""
        if not sym or not d_str:
            continue
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue
        events.append({
            "symbol": sym,
            "catalyst_type": "ipo",
            "event_date": d,
            "event_time": "bmo",
            "title": f"{sym} IPO  ({it.get('exchange','?')})",
            "payload": {
                "name": it.get("name"), "shares": it.get("numberOfShares"),
                "price": it.get("price"),
            },
        })
    return catalysts_repo.upsert_many(db_path, events)


def _seed_macro_events(db_path: Path, *, days_ahead: int) -> int:
    today = date.today()
    until = today + timedelta(days=days_ahead)
    events = []
    for ev in _2026_EVENTS + _nfp_dates(today.year) + _nfp_dates(today.year + 1):
        if not (today <= ev.on <= until):
            continue
        # Map known macro events to a stable "symbol" so they're queryable.
        # Convention: SPY/QQQ are the "broad market" representatives.
        events.append({
            "symbol": "SPY",
            "catalyst_type": "macro",
            "event_date": ev.on,
            "event_time": "bmo" if ev.name == "CPI" or ev.name == "NFP" else None,
            "title": f"{ev.name}",
            "payload": {"name": ev.name, "release_time_et": ev.release_time_et.isoformat()},
        })
    return catalysts_repo.upsert_many(db_path, events)
